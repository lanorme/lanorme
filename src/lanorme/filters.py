"""Post-run narrowing of check results: targets, rule codes, excludes, silencers, promotion.

These operate purely on the ``CheckResult`` list a run produces. They carry no
argument-parsing or config-discovery concern, so they live apart from
``cli.py`` and ``runner.py`` (which drive them). :class:`Narrowing` applies the
stages in their fixed order and reports what each one dropped; every stage
returns a new list with statuses recomputed and none mutates its input.
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from lanorme import CheckResult, Violation, extract_code
from lanorme.directives import is_silenced_inline
from lanorme.regions import reanchor_results
from lanorme.selectors import is_code_matched
from lanorme.source_lines import SourceLines

logger = logging.getLogger(__name__)


def count_findings(results: list[CheckResult]) -> int:
    """Every violation and warning across *results*."""
    return sum(len(r.violations) + len(r.warnings) for r in results)


def _is_path_excluded(*, path: str, patterns: list[str]) -> bool:
    normalised = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalised, pattern) for pattern in patterns)


def _is_silenced_per_file(*, file: str, rule: str, table: dict[str, list[str]]) -> bool:
    """True if *rule* (full code or category) is silenced for *file* by *table*."""
    code = extract_code(rule)
    normalised = file.replace("\\", "/")
    return any(
        fnmatch.fnmatch(normalised, pattern) and is_code_matched(code=code, patterns=codes)
        for pattern, codes in table.items()
    )


@dataclass(frozen=True)
class Narrowed:
    """The results that survived a :class:`Narrowing`, and what each stage dropped.

    ``dropped`` maps a stage name (``targets``, ``selection``, ``exclude``,
    ``per_file``, ``inline``) to the number of findings it removed.
    """

    results: list[CheckResult]
    dropped: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Narrowing:
    """The result-narrowing a run applies after the checks, in its fixed order.

    1. ``targets``: only findings for the explicitly requested files and
       directories (in *run_root*-relative paths); ``None`` keeps everything,
       since the discovery scope already confined the walk. The survivors are
       then re-expressed relative to *project_root*, where every later stage
       (config globs, the inline-ignore source lookup, display) works.
    2. ``selection``: the rule codes *select* keeps and *ignore* drops.
    3. ``exclude``: findings in a file an *exclude* glob matches.
    4. ``per_file``: the ``[tool.lanorme.per-file-ignores]`` table.
    5. ``inline``: a covering ``# noqa`` / ``# lanorme: ignore`` on the line,
       read through *lines* (a fresh cache over *project_root* when not given).
    """

    select: list[str]
    ignore: list[str]
    exclude: list[str]
    per_file_ignores: dict[str, list[str]]
    project_root: Path
    targets: list[Path] | None = None
    run_root: Path | None = None
    lines: SourceLines | None = None

    def apply(self, results: list[CheckResult]) -> Narrowed:
        """Run every stage over *results*, counting what each one drops.

        A stage with nothing to apply (no targets, an empty table) is skipped
        and counted as dropping nothing.
        """
        dropped: dict[str, int] = {}
        for stage, keep in self._build_stages():
            before = count_findings(results)
            if keep is not None:
                results = [result.filter_findings(keep) for result in results]
            dropped[stage] = before - count_findings(results)
            if stage == "targets":
                results = reanchor_results(
                    results=results,
                    from_root=self.run_root or self.project_root,
                    to_root=self.project_root,
                )
        return Narrowed(results=results, dropped=dropped)

    def _build_stages(self) -> list[tuple[str, Callable[[Violation], bool] | None]]:
        return [
            ("targets", self._build_target_filter()),
            ("selection", self._keep_selected if self.select or self.ignore else None),
            ("exclude", self._keep_unexcluded if self.exclude else None),
            ("per_file", self._keep_unsilenced_per_file if self.per_file_ignores else None),
            ("inline", self._build_inline_filter()),
        ]

    def _build_target_filter(self) -> Callable[[Violation], bool] | None:
        if not self.targets:
            return None
        run_root = self.run_root or self.project_root
        files = {t.resolve() for t in self.targets if t.is_file()}
        dirs = {t.resolve() for t in self.targets if t.is_dir()}

        def is_targeted(finding: Violation) -> bool:
            if not finding.file:
                return True  # a RUN-000 crash notice belongs to no path; never drop it
            absolute = (run_root / finding.file).resolve()
            return absolute in files or any(absolute == d or d in absolute.parents for d in dirs)

        return is_targeted

    def _keep_selected(self, finding: Violation) -> bool:
        code = extract_code(finding.rule)
        selected = not self.select or is_code_matched(code=code, patterns=self.select)
        return selected and not is_code_matched(code=code, patterns=self.ignore)

    def _keep_unexcluded(self, finding: Violation) -> bool:
        return not _is_path_excluded(path=finding.file, patterns=self.exclude)

    def _keep_unsilenced_per_file(self, finding: Violation) -> bool:
        return not _is_silenced_per_file(
            file=finding.file,
            rule=finding.rule,
            table=self.per_file_ignores,
        )

    def _build_inline_filter(self) -> Callable[[Violation], bool]:
        lines = self.lines or SourceLines(self.project_root)

        def is_unsilenced(finding: Violation) -> bool:
            line = lines.read_line(file=finding.file, line=finding.line)
            return not is_silenced_inline(line=line, rule=finding.rule)

        return is_unsilenced


def note_excluded_targets(
    *,
    targets: list[Path] | None,
    project_root: Path,
    exclude: list[str],
) -> None:
    """Say so on stderr when every requested path falls under an exclude glob.

    A file target inside an excluded tree otherwise reports a clean run with
    no hint that nothing was scanned.
    """
    if not targets or not exclude:
        return
    root = project_root.resolve()
    for target in targets:
        try:
            relative = target.resolve().relative_to(root).as_posix()
        except ValueError:
            return
        covered = _is_path_excluded(path=relative, patterns=exclude) or _is_path_excluded(
            path=relative + "/",
            patterns=exclude,
        )
        if not covered:
            return
    logger.warning(
        "every requested path matches an exclude glob, so nothing was checked. "
        "Pass --exclude with another glob to override the configured excludes for one run.",
    )


def apply_promotions(*, results: list[CheckResult], promote: list[str]) -> list[CheckResult]:
    """Promote advisory warnings whose code matches *promote* into violations.

    Lets a project escalate heuristic, default-warning rules (for example
    ``TYPE-004`` or ``SIMILAR-001``) into build-failing errors via
    ``[tool.lanorme] promote`` or ``--promote`` (a code, a category, or
    ``ALL``). Promotion runs last, so a warning already silenced by
    ``ignore`` / ``per-file-ignores`` / ``# noqa`` is gone and never promoted.
    """
    if not promote:
        return results

    promoted_results: list[CheckResult] = []
    for result in results:
        escalated: list[Violation] = []
        kept: list[Violation] = []
        for warning in result.warnings:
            code = extract_code(warning.rule)
            # ``-000`` codes are skip/parse-error notices ("could not analyse,
            # skipping"), not findings, so promotion (including ``ALL``) leaves
            # them as warnings rather than failing the build on a non-issue.
            if not code.endswith("-000") and is_code_matched(code=code, patterns=promote):
                escalated.append(replace(warning, promoted=True))
            else:
                kept.append(warning)
        promoted_results.append(
            CheckResult.from_findings(
                check=result.check,
                violations=[*result.violations, *escalated],
                warnings=kept,
            ),
        )
    return promoted_results
