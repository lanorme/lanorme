"""Post-run filtering of check results: rule-code matching, ``# noqa``, promotion.

These helpers operate purely on the ``CheckResult`` list a run produces. They
carry no argument-parsing or config-discovery concern, so they live apart from
``cli.py`` (which orchestrates them). Each ``_apply_*`` returns a new list with
statuses recomputed; none mutates its input.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import replace
from pathlib import Path

from lanorme import CheckResult, Violation, extract_code

logger = logging.getLogger(__name__)

_CODE_RE = re.compile(r"^([A-Z]+)-\d+")


def _extract_category(code: str) -> str:
    """The category prefix of a code, e.g. 'LAYER' from 'LAYER-002'."""
    match = _CODE_RE.match(code)
    return match.group(1) if match else code


def _matches(*, code: str, patterns: list[str]) -> bool:
    """True if *code* matches any selector (exact code, category, or 'ALL').

    Case-insensitive, and whitespace and empty entries are ignored, so a
    selector from a config list (``[" type-004 "]``) behaves like the CLI form
    (``--promote 'type-004'``), which is normalised by ``_split_csv``.
    """
    code_upper = code.upper()
    category = _extract_category(code_upper)
    wanted = {p.strip().upper() for p in patterns if p.strip()}
    return any(p in ("ALL", code_upper, category) for p in wanted)


def _keep(*, rule: str, select: list[str], ignore: list[str]) -> bool:
    code = extract_code(rule)
    selected = not select or _matches(code=code, patterns=select)
    return selected and not _matches(code=code, patterns=ignore)


# --------------------------------------------------------------------------- #
# Rule-code selection, path targets, excludes, per-file-ignores
# --------------------------------------------------------------------------- #


def _apply_filters(
    *,
    results: list[CheckResult],
    select: list[str],
    ignore: list[str],
) -> list[CheckResult]:
    """Drop violations/warnings whose rule code is deselected, recompute status."""
    if not select and not ignore:
        return results
    return [
        result.filter_findings(
            lambda finding: _keep(rule=finding.rule, select=select, ignore=ignore),
        )
        for result in results
    ]


def _apply_target_filter(
    *,
    results: list[CheckResult],
    run_root: Path,
    targets: list[Path] | None,
) -> list[CheckResult]:
    """Keep only findings for the explicitly requested files/dirs.

    The checks run from *run_root* (the project root) so cross-file checks see
    the whole project; this narrows output to the requested paths so a file
    target reports that file alone. ``None`` (a lone directory request) keeps
    everything: the discovery scope already confined the walk to it.
    """
    if not targets:
        return results

    files = {t.resolve() for t in targets if t.is_file()}
    dirs = {t.resolve() for t in targets if t.is_dir()}

    def should_keep(finding: Violation) -> bool:
        if not finding.file:
            return True  # a RUN-000 crash notice belongs to no path; never drop it
        absolute = (run_root / finding.file).resolve()
        return absolute in files or any(absolute == d or d in absolute.parents for d in dirs)

    return [result.filter_findings(should_keep) for result in results]


def _is_path_excluded(*, path: str, patterns: list[str]) -> bool:
    normalised = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalised, pattern) for pattern in patterns)


def _apply_excludes(*, results: list[CheckResult], exclude: list[str]) -> list[CheckResult]:
    """Drop violations/warnings whose file path matches an exclude glob."""
    if not exclude:
        return results
    return [
        result.filter_findings(
            lambda finding: not _is_path_excluded(path=finding.file, patterns=exclude),
        )
        for result in results
    ]


def _is_silenced_per_file(*, file: str, rule: str, table: dict[str, list[str]]) -> bool:
    """True if *rule* (full code or category) is silenced for *file* by *table*."""
    code = extract_code(rule)
    normalised = file.replace("\\", "/")
    for pattern, codes in table.items():
        if fnmatch.fnmatch(normalised, pattern) and _matches(code=code, patterns=codes):
            return True
    return False


def _apply_per_file_ignores(
    *,
    results: list[CheckResult],
    table: dict[str, list[str]],
) -> list[CheckResult]:
    """Drop findings whose ``(file, rule)`` pair is silenced by the per-file-ignores table."""
    if not table:
        return results
    return [
        result.filter_findings(
            lambda finding: (
                not _is_silenced_per_file(file=finding.file, rule=finding.rule, table=table)
            ),
        )
        for result in results
    ]


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


# --------------------------------------------------------------------------- #
# Inline suppression: the ``noqa`` and ``lanorme: ignore[...]`` comments
# --------------------------------------------------------------------------- #

_NOQA_RE = re.compile(r"#\s*noqa(?:\s*:\s*([A-Za-z0-9_,\-\s]+))?", re.IGNORECASE)
# A LaNorme-native directive ruff and other linters never read, so a project
# running both can silence a finding without ruff reporting an invalid `noqa`
# (ruff's parser cannot tokenise the hyphen in our codes, e.g. ``TYPE-001``).
_IGNORE_RE = re.compile(
    r"#\s*lanorme\s*:\s*ignore(?:\s*\[([A-Za-z0-9_,\-\s]+)\])?",
    re.IGNORECASE,
)


def _is_silenced_by_directive(*, pattern: re.Pattern[str], line: str, rule: str) -> bool:
    """True if a *pattern* directive on *line* covers *rule*.

    A directive with no code list (bare ``# noqa`` or ``# lanorme: ignore``)
    silences every rule on the line; a coded one silences only matching codes
    (exact code, category, or ``ALL``).
    """
    match = pattern.search(line)
    if match is None:
        return False
    if match.group(1) is None:
        return True
    codes = [c.strip() for c in match.group(1).split(",") if c.strip()]
    return _matches(code=extract_code(rule), patterns=codes)


# Categories no inline directive may silence. A budget on suppressions that an
# offender can waive on the offending line is not a budget, so the SUPPRESS
# family is answerable only in config, where the decision is reviewable.
_UNSUPPRESSABLE = frozenset({"SUPPRESS"})


def _is_silenced_inline(*, line: str, rule: str) -> bool:
    """True if a ``# noqa`` or ``# lanorme: ignore`` on *line* covers *rule*."""
    if _extract_category(extract_code(rule)) in _UNSUPPRESSABLE:
        return False
    return _is_silenced_by_directive(
        pattern=_NOQA_RE,
        line=line,
        rule=rule,
    ) or _is_silenced_by_directive(
        pattern=_IGNORE_RE,
        line=line,
        rule=rule,
    )


def _read_line(*, project_root: Path, file: str, line: int, cache: dict[str, list[str]]) -> str:
    """Read source line *line* from *file*, caching the file's lines for the run."""
    key = file.replace("\\", "/")
    lines = cache.get(key)
    if lines is None:
        path = project_root / file
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        cache[key] = lines
    if not lines or line <= 0 or line > len(lines):
        return ""
    return lines[line - 1]


def _apply_inline_ignores(*, results: list[CheckResult], project_root: Path) -> list[CheckResult]:
    """Drop findings whose source line carries a covering ``# noqa`` / ``# lanorme: ignore``."""
    cache: dict[str, list[str]] = {}

    def should_keep(violation: Violation) -> bool:
        line = _read_line(
            project_root=project_root,
            file=violation.file,
            line=violation.line,
            cache=cache,
        )
        return not _is_silenced_inline(line=line, rule=violation.rule)

    return [result.filter_findings(should_keep) for result in results]


# --------------------------------------------------------------------------- #
# Severity promotion
# --------------------------------------------------------------------------- #


def _apply_promotions(*, results: list[CheckResult], promote: list[str]) -> list[CheckResult]:
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
            if not code.endswith("-000") and _matches(code=code, patterns=promote):
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
