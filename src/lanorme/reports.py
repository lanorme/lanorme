"""Output rendering for the LaNorme CLI.

Everything that turns check results into text lives here: the four ``check``
output formats (``concise``, ``full``, ``json``, ``ndjson``), the ``rules``
listing, the ``rule CODE`` reference lookup, and the ``--show-config`` dump.
Keeping rendering separate from orchestration keeps ``cli.py`` focused on
parsing arguments and running checks.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

from lanorme import CheckResult, Status, Violation, get_all_checks
from lanorme.baseline import compute_fingerprint


@dataclasses.dataclass(frozen=True)
class RunNotes:
    """What the run did around the findings, for the summary and the records."""

    project_root: Path
    selected_checks: tuple[str, ...] | None = None
    suppressed_inline: int = 0
    suppressed_per_file: int = 0
    suppressed_baseline: int = 0
    baseline_configured: bool = False

    @property
    def opt_in_disabled(self) -> int:
        """Selected checks that ship off and were not enabled for this run.

        ``selected_checks`` is the run's selection (every check for a full
        run, the one named under ``--check``); ``None`` counts the registry.
        """
        checks = get_all_checks()
        names = checks if self.selected_checks is None else self.selected_checks
        return sum(
            1
            for name in names
            if name in checks and hasattr(checks[name], "enabled") and not checks[name].enabled
        )


@contextlib.contextmanager
def tolerate_closed_pipe() -> Iterator[None]:
    """Let a reader that stops early (``| head``, ``| jq -n``) end the output quietly.

    Without this a closed pipe surfaces as a ``BrokenPipeError`` traceback, or
    (when stdout is block-buffered, the normal case for a pipe) as an
    "Exception ignored" message and exit 120 from the interpreter's final
    flush. The output is flushed inside the guard so the failure is seen here,
    and stdout is then pointed at the null device so the final flush has
    nowhere to fail. The caller's exit code is unaffected.
    """
    try:
        yield
        sys.stdout.flush()
    except BrokenPipeError:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass


# --------------------------------------------------------------------------- #
# Check results
# --------------------------------------------------------------------------- #


def _build_finding_records(
    *,
    result: CheckResult,
    project_root: Path | None,
    cache: dict[str, list[str]],
) -> list[dict[str, object]]:
    """Flatten a check result into one record per finding (violations + warnings)."""
    records: list[dict[str, object]] = []
    for severity, items in (("error", result.violations), ("warning", result.warnings)):
        for finding in items:
            record: dict[str, object] = {
                "check": result.check,
                "severity": severity,
                **finding.to_dict(),
            }
            if project_root is not None:
                record["fingerprint"] = compute_fingerprint(
                    project_root=project_root,
                    finding=finding,
                    cache=cache,
                )
            records.append(record)
    return records


def _emit_ndjson(*, results: list[CheckResult], project_root: Path | None) -> None:
    """Print one JSON object per finding, newline-delimited (grep/jq friendly)."""
    cache: dict[str, list[str]] = {}
    for result in results:
        for record in _build_finding_records(result=result, project_root=project_root, cache=cache):
            print(json.dumps(record))


def _emit_json(*, results: list[CheckResult], project_root: Path | None) -> None:
    """Print one object per check, each finding carrying its fingerprint."""
    cache: dict[str, list[str]] = {}
    payload = []
    for result in results:
        records = _build_finding_records(result=result, project_root=project_root, cache=cache)
        payload.append(
            {
                "check": result.check,
                "status": result.status.value,
                "violations": [r for r in records if r["severity"] == "error"],
                "warnings": [r for r in records if r["severity"] == "warning"],
            },
        )
    print(json.dumps(payload, indent=2))


def _emit_summary(*, results: list[CheckResult]) -> None:
    """Counts only: by code, by top-level directory, and the totals. For large trees."""
    by_code: dict[tuple[str, str], int] = {}
    by_dir: dict[str, int] = {}
    for result in results:
        for severity, items in (("error", result.violations), ("warning", result.warnings)):
            for finding in items:
                by_code[(finding.code, severity)] = by_code.get((finding.code, severity), 0) + 1
                top = finding.file.split("/", 1)[0] if "/" in finding.file else "."
                by_dir[top] = by_dir.get(top, 0) + 1
    _print_totals(results=results)
    if by_code:
        print("By code:")
        for (code, severity), count in sorted(
            by_code.items(),
            key=lambda item: (-item[1], item[0]),
        ):
            print(f"  {code:<16} {severity:<8} {count}")
        print("By directory:")
        for directory, count in sorted(by_dir.items(), key=lambda item: (-item[1], item[0])):
            print(f"  {directory + '/':<24} {count}")


def _emit_human(*, results: list[CheckResult], show_passed: bool, notes: RunNotes | None) -> None:
    """Print the human report.

    ``full`` (*show_passed* true) reproduces the verbose per-check listing exactly,
    with no summary footer. ``concise`` (*show_passed* false) prints only the checks
    that found something, then the summary so an empty run is not silent.
    """
    shown = 0
    for result in results:
        if not show_passed and result.status == Status.PASS:
            continue
        print(result.format_human())
        print()
        shown += 1

    if show_passed:
        return
    if shown == 0:
        print(f"All {len(results)} checks passed.")
    else:
        _print_totals(results=results)
    if notes is not None:
        _print_notes(results=results, notes=notes)


def _print_totals(*, results: list[CheckResult]) -> None:
    passed = sum(1 for r in results if r.status == Status.PASS)
    warned = sum(1 for r in results if r.status == Status.WARN)
    failed = sum(1 for r in results if r.status == Status.FAIL)
    print(f"Summary: {len(results)} checks — {passed} passed, {warned} warned, {failed} failed.")
    errors = sum(len(r.violations) for r in results)
    advisories = sum(len(r.warnings) for r in results)
    print(
        f"Findings: {errors} {_pluralise(count=errors, noun='error')} to fix, "
        f"{advisories} advisory {_pluralise(count=advisories, noun='warning')}.",
    )


# Past this many errors with no baseline, the adoption path is worth a line.
_BASELINE_TIP_THRESHOLD = 25


def _print_notes(*, results: list[CheckResult], notes: RunNotes) -> None:
    """What a clean or dirty summary would otherwise hide: suppressions, opt-ins, adoption."""
    suppressed = (notes.suppressed_inline, notes.suppressed_per_file, notes.suppressed_baseline)
    if any(suppressed):
        print(
            f"Suppressed: {suppressed[0]} by inline ignores, {suppressed[1]} by per-file-ignores, "
            f"{suppressed[2]} by the baseline.",
        )
    if notes.opt_in_disabled:
        print(
            f"Opt-in checks not enabled: {notes.opt_in_disabled} "
            "('lanorme check --show-config' lists them).",
        )
    errors = sum(len(r.violations) for r in results)
    if errors >= _BASELINE_TIP_THRESHOLD and not notes.baseline_configured:
        print(
            "Tip: 'lanorme baseline write' records today's findings as debt so that only "
            "new ones report (see the adoption tutorial).",
        )


def _pluralise(*, count: int, noun: str) -> str:
    return noun if count == 1 else f"{noun}s"


def _escape_for_github(text: str) -> str:
    """Escape annotation message data per the GitHub workflow-command spec."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _emit_github(*, results: list[CheckResult]) -> None:
    """Emit GitHub Actions workflow commands so findings appear inline on PR diffs.

    The ``title`` uses the rule *code* (``DRY-001``), not the full rule string:
    annotation properties are comma-separated and terminated by ``::``, so a
    title carrying the rule's ``: `` description or a comma would corrupt the
    annotation. The message keeps its full text with newlines encoded as ``%0A``.
    """
    for result in results:
        for v in result.violations:
            print(
                f"::error {_format_github_location(v)},title={v.code}::{_escape_for_github(v.message)}",
            )
        for w in result.warnings:
            print(
                f"::warning {_format_github_location(w)},title={w.code}::{_escape_for_github(w.message)}",
            )


def _format_github_location(finding: Violation) -> str:
    """The annotation's location properties, with the span when the check knows it."""
    parts = [f"file={finding.file}", f"line={finding.line}"]
    if finding.end_line is not None:
        parts.append(f"endLine={finding.end_line}")
    if finding.column is not None:
        # Workflow-command columns are 1-based; ours follow ``ast`` (0-based).
        parts.append(f"col={finding.column + 1}")
    if finding.end_column is not None:
        parts.append(f"endColumn={finding.end_column + 1}")
    return ",".join(parts)


def resolve_output_format(*, explicit: str | None, as_json: bool) -> str:
    """Pick the output format: ``--json`` wins, then an explicit ``--output-format``.

    Only an unset format auto-detects, choosing ``github`` inside GitHub Actions
    (so findings annotate the PR diff) and ``concise`` otherwise. An explicit
    choice is always honoured, even in CI.
    """
    if as_json:
        return "json"
    if explicit is not None:
        return explicit
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "github"
    return "concise"


def print_baseline_drift(*, drifted: list[tuple[str, str]], output_format: str) -> None:
    """Explain findings whose baseline entry stopped matching them.

    Without this, an upgrade that rewords a rule description presents recorded
    debt as a fresh violation: the code did not change, the anchor did. The
    machine-readable formats stay a pure finding stream, so the note is for the
    human ones only.
    """
    if not drifted or output_format in {"json", "ndjson", "github", "summary"}:
        return
    count = len(drifted)
    print(
        f"\nNote: {count} finding{'' if count == 1 else 's'} above "
        f"{'has' if count == 1 else 'have'} a baseline entry that no longer matches:",
    )
    for file, code in drifted:
        print(f"  {file}  {code}")
    print(
        "  The baseline records these already, so this is existing debt rather "
        "than new.\n"
        "  Run 'lanorme baseline status' to confirm, then 'lanorme baseline "
        "write' to re-anchor.",
    )


def emit(*, results: list[CheckResult], output_format: str, notes: RunNotes | None = None) -> None:
    """Dispatch results to the requested output format."""
    project_root = notes.project_root if notes is not None else None
    if output_format == "json":
        _emit_json(results=results, project_root=project_root)
    elif output_format == "ndjson":
        _emit_ndjson(results=results, project_root=project_root)
    elif output_format == "github":
        _emit_github(results=results)
    elif output_format == "summary":
        _emit_summary(results=results)
    else:
        _emit_human(results=results, show_passed=output_format == "full", notes=notes)


# --------------------------------------------------------------------------- #
# Effective configuration (--show-config)
# --------------------------------------------------------------------------- #


def _summarise_settings(check: object) -> str:
    """One-line summary of a check's effective settings after configuration."""
    if not dataclasses.is_dataclass(check):
        return ""
    parts: list[str] = []
    if hasattr(check, "enabled"):
        parts.append(f"enabled={check.enabled}")
    for field in dataclasses.fields(check):
        if field.name in {"name", "description", "rules", "enabled"}:
            continue
        value = getattr(check, field.name)
        text = repr(value)
        if len(text) > 48:
            if isinstance(value, (list, tuple, set, frozenset)):
                text = f"<{len(value)} items>"
            elif isinstance(value, dict):
                text = f"<{len(value)} keys>"
            else:
                text = text[:45] + "..."
        parts.append(f"{field.name}={text}")
    summary = " ".join(parts)
    if hasattr(check, "enabled") and not check.enabled:
        summary += "   (opt-in, not enabled)"
    keys = getattr(check, "settings_keys", None)
    if keys:
        summary += f"\n{'':<21}keys: {', '.join(sorted(keys))}"
    return summary


_TOP_LEVEL_KEYS = (
    "extends",
    "select",
    "ignore",
    "promote",
    "exclude",
    "baseline",
    "source_root",
    "plugins",
)


def print_config(
    *,
    config: dict[str, object],
    source: str | None,
    project_root: Path,
    extends: object = None,
) -> None:
    """Print the discovered config file and the effective settings for every check.

    *extends* is the raw ``extends`` value: profile resolution folds it into
    *config*, so it is passed separately to show where promoted or ignored
    codes came from.
    """
    if source is None:
        print(
            "config file:  none, built-in defaults (looked for lanorme.toml, .lanorme.toml "
            f"and a [tool.lanorme] table in pyproject.toml from {project_root} upwards)",
        )
    else:
        print(f"config file:  {source}")
    print(f"project root: {project_root}")
    shown = {"extends": extends} if extends else {}
    shown.update((k, config[k]) for k in _TOP_LEVEL_KEYS if k in config)
    if shown or config.get("per-file-ignores"):
        print("\n[tool.lanorme]")
        for key, value in shown.items():
            print(f"  {key} = {value!r}")
        if config.get("per-file-ignores"):
            print(f"  per-file-ignores = {config['per-file-ignores']!r}")
    print("\nchecks (effective settings):")
    for name, check in sorted(get_all_checks().items()):
        print(f"  {name:<18} {_summarise_settings(check)}")
