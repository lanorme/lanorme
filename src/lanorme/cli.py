"""Command-line entry point for LaNorme.

    lanorme check [PATHS...] [--check NAME|CODE] [--select ...] [--ignore ...]
                  [--exclude ...] [--output-format {concise,full,json,ndjson,github}]
                  [--json] [--plugin MODULE]
    lanorme rules
    lanorme rule CODE
    lanorme --version

Configuration is discovered by walking up from the target path: a dedicated
``lanorme.toml`` takes precedence, otherwise a ``[tool.lanorme]`` table in
``pyproject.toml``. CLI flags override config.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import pkgutil
import sys
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path

import lanorme.checks
from lanorme import baseline, reference, reports
from lanorme import (
    Check,
    CheckResult,
    ResultAuditor,
    Status,
    __version__,
    get_all_checks,
    get_check,
    extract_code,
    run_all,
    run_audit,
    run_check,
)
from lanorme.checkconfig import apply_check_config
from lanorme.diagnostics import configure_diagnostics
from lanorme.errors import UsageError
from lanorme.filters import _apply_promotions, note_excluded_targets
from lanorme.presets import _resolve_extends
from lanorme.selectors import checks_for_selector, reject_unknown_selectors
from lanorme.regions import discover_config, restore_defaults, snapshot_defaults
from lanorme.runner import Filters, collect_results, count_findings

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Check discovery
# --------------------------------------------------------------------------- #


def _load_builtin_checks() -> None:
    """Import every module under ``lanorme.checks`` so they self-register."""
    for mod in pkgutil.iter_modules(lanorme.checks.__path__, prefix="lanorme.checks."):
        importlib.import_module(mod.name)


def _load_entry_point_checks() -> None:
    """Import plugin modules advertised under the ``lanorme.checks`` group."""
    for ep in entry_points(group="lanorme.checks"):
        importlib.import_module(ep.value.split(":")[0])


def _load_plugin_modules(modules: list[str]) -> None:
    """Import explicitly-named plugin modules so they self-register."""
    for module in modules:
        importlib.import_module(module)



# --------------------------------------------------------------------------- #
# Configuration discovery
# --------------------------------------------------------------------------- #


def _resolve_single(*, selector: str) -> tuple[list[Check], list[str]]:
    """The check(s) named or coded by *selector*, and the implicit code narrowing.

    Resolution is name-first (an exact check name like ``duplication``), then by
    rule code or category (``DRY-001`` / ``SIZE``, case-insensitive). When a code
    or category is given, it is returned as an implicit selector so the output is
    narrowed to that code, and only the owning check(s) run. Exits 2 if unknown.
    """
    by_name = get_check(selector)
    if by_name is not None:
        _note_disabled_selection(only=[by_name])
        return [by_name], []

    matched = checks_for_selector(selector=selector)
    if matched:
        _note_disabled_selection(only=matched)
        return matched, [selector.upper()]

    names = ", ".join(sorted(get_all_checks())) or "(none)"
    raise UsageError(
        f"'{selector}' is not a known check name, rule code, or category.\n"
        f"  Checks: {names}\n"
        f"  Run 'lanorme rules' to see every rule code and category."
    )


def _resolve_targets(paths: list[str]) -> tuple[Path, list[Path] | None]:
    """Resolve the CLI path arguments to ``(scan_root, targets)``.

    *scan_root* is the directory checks walk and relativise findings against.
    *targets* is the requested files/dirs used to narrow findings, or ``None``
    for a single directory (the whole tree is reported, exactly as before).

    Behind issue #17: ``os.walk`` over a file yields nothing, so a file target
    made every tree-walking check silently find zero. Instead we walk the file's
    directory (the scope ``lanorme check <dir>`` already uses) then post-filter.
    """
    resolved = [Path(p) for p in paths]
    for path in resolved:
        if not path.exists():
            raise UsageError(f"path '{path}' does not exist.")

    if len(resolved) == 1 and resolved[0].is_dir():
        return resolved[0], None

    try:
        common = Path(os.path.commonpath([str(p.resolve()) for p in resolved]))
    except ValueError as error:
        raise UsageError("cannot check paths located on different drives.") from error
    return (common.parent if common.is_file() else common), resolved


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()] if value else []


def _read_config_list(value: object) -> list[str]:
    """Normalise a config selector value to a list of strings.

    Accepts a list (``promote = ["TYPE-004"]``) or a bare string
    (``promote = "ALL"``); anything else yields ``[]``. Whitespace and case are
    handled downstream by ``_matches``, matching the CLI ``_split_csv`` path.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lanorme",
        description="La norme — architecture & code-quality linter for Python.",
    )
    parser.add_argument("--version", action="version", version=f"lanorme {__version__}")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser(
        "check",
        help="Run checks against one or more paths.",
        epilog=(
            "Silence a finding on its line with '# lanorme: ignore[CODE]' (or '# noqa: CODE'), "
            "a file pattern with [tool.lanorme.per-file-ignores] (\"tests/*\" = [\"CODE\"]), "
            "or record today's findings as debt with 'lanorme baseline write'. "
            "'lanorme rule CODE' explains a code."
        ),
    )
    check.add_argument("paths", nargs="*", default=["."], help="Path(s) to check (default: .)")
    check.add_argument(
        "--check",
        dest="single",
        default=None,
        help="Run a single check by name (e.g. duplication), or by rule code/category (e.g. DRY-001, SIZE).",
    )
    check.add_argument("--select", default=None, help="Comma-separated rule codes/categories to run.")
    check.add_argument("--ignore", default=None, help="Comma-separated rule codes/categories to skip.")
    check.add_argument("--exclude", default=None, help="Comma-separated file-path globs to exclude.")
    check.add_argument(
        "--promote",
        default=None,
        help="Comma-separated rule codes/categories whose warnings become build-failing errors (or ALL).",
    )
    check.add_argument(
        "--show-config",
        action="store_true",
        help="Print the discovered config and effective per-check settings, then exit.",
    )
    check.add_argument("--plugin", action="append", default=[], help="Plugin module to load (repeatable).")
    check.add_argument(
        "--output-format",
        choices=["concise", "full", "json", "ndjson", "github", "summary"],
        default=None,
        help=(
            "Output format (default: concise). 'concise' shows only checks with findings plus a "
            "summary; 'full' shows every check; 'json' is one object per check; 'ndjson' is one "
            "finding per line; 'github' emits workflow commands (auto-detected when GITHUB_ACTIONS=true); "
            "'summary' prints counts by code and directory only."
        ),
    )
    check.add_argument("--json", action="store_true", help="Alias for --output-format=json.")
    check.add_argument(
        "--no-baseline",
        action="store_true",
        help="Ignore the configured baseline for this run (report the whole debt).",
    )

    bl = sub.add_parser("baseline", help="Record or inspect the warning baseline.")
    bl.add_argument(
        "action",
        choices=["write", "status"],
        help="'write' records current findings; 'status' lists stale entries.",
    )
    bl.add_argument("paths", nargs="*", default=["."], help="Project root to scan (default: .)")

    rules = sub.add_parser("rules", help="List all registered rules and exit.")
    rules.add_argument("--json", action="store_true", help="Emit the listing as JSON.")

    rule = sub.add_parser("rule", help="Print the reference section for a single rule code.")
    rule.add_argument("code", help="The rule code to look up (e.g. CMT-001, SQL-001).")
    rule.add_argument("--json", action="store_true", help="Emit the declaration and section as JSON.")

    return parser


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def _note_disabled_selection(*, only: list[Check]) -> None:
    """Say so when every selected check is opt-in and not enabled: the run would be silent."""
    disabled = [c.name for c in only if hasattr(c, "enabled") and not getattr(c, "enabled")]
    if len(disabled) != len(only):
        return
    names = ", ".join(disabled)
    logger.warning(
        "%s is opt-in and not enabled, so it reports nothing. Enable it with "
        "[tool.lanorme.%s] enabled = true (see 'lanorme rule' for its codes).",
        names,
        disabled[0],
    )


def _resolve_baseline_path(*, config: dict[str, object], project_root: Path) -> Path | None:
    """The configured baseline file path, or ``None`` when no baseline is set."""
    configured = config.get("baseline")
    return project_root / str(configured) if configured else None


def _run_and_report(
    *,
    args: argparse.Namespace,
    config: dict[str, object],
    scan_root: Path,
    project_root: Path,
    targets: list[Path] | None,
    pristine: dict[str, object],
) -> None:
    """Run the selected checks, apply the filters, print, and set the exit code."""
    ignore = _split_csv(args.ignore) or _read_config_list(config.get("ignore"))
    exclude = _split_csv(args.exclude) or _read_config_list(config.get("exclude"))
    promote = _split_csv(args.promote) or _read_config_list(config.get("promote"))
    select = _split_csv(args.select) or _read_config_list(config.get("select"))
    output_format = reports.resolve_output_format(explicit=args.output_format, as_json=args.json)
    reject_unknown_selectors(selectors=promote, origin="'promote'")

    collected = collect_results(
        config=config, scan_root=scan_root, project_root=project_root, targets=targets,
        pristine=pristine,
        filters=Filters(single=args.single, select=select, ignore=ignore, exclude=exclude),
        resolve_single=_resolve_single,
    )
    if collected is None:
        print("No checks registered.")
        return
    note_excluded_targets(targets=targets, project_root=project_root, exclude=exclude)

    results = collected.results
    drifted: list[tuple[str, str]] = []
    baselined = 0
    if not args.no_baseline:
        before = count_findings(results)
        results, drifted = _apply_baseline(results=results, config=config, project_root=project_root)
        baselined = before - count_findings(results)

    results = _apply_promotions(results=results, promote=promote)
    notes = reports.RunNotes(
        project_root=project_root,
        suppressed_inline=collected.suppressed_inline,
        suppressed_per_file=collected.suppressed_per_file,
        suppressed_baseline=baselined,
        baseline_configured=_resolve_baseline_path(config=config, project_root=project_root) is not None,
    )
    failed = any(r.status == Status.FAIL for r in results)
    with reports.tolerate_closed_pipe():
        reports.emit(results=results, output_format=output_format, notes=notes)
        reports.print_baseline_drift(drifted=drifted, output_format=output_format)
    if failed:
        sys.exit(1)


def _apply_baseline(
    *, results: list[CheckResult], config: dict[str, object], project_root: Path
) -> tuple[list[CheckResult], list[tuple[str, str]]]:
    """Suppress the configured baseline's findings; return the survivors and the drift."""
    baseline_path = _resolve_baseline_path(config=config, project_root=project_root)
    if baseline_path is None:
        return results, []
    if not baseline_path.exists():
        raise UsageError(
            f"baseline file '{baseline_path}' does not exist. Run 'lanorme baseline write' first."
        )
    # Drift reads the raw findings: it has to see what the baseline did
    # match to tell a moved anchor from debt that is genuinely new.
    drifted = baseline.find_drifted_codes(
        results=results, project_root=project_root, baseline_path=baseline_path
    )
    suppressed = baseline.suppress(
        results=results, project_root=project_root, baseline_path=baseline_path
    )
    return suppressed, drifted


def _run_check_command(*, args: argparse.Namespace) -> None:
    """Handle the ``check`` subcommand: discover config, then report or run."""
    scan_root, targets = _resolve_targets(args.paths)

    found = discover_config(start=scan_root, resolve_extends=_resolve_extends)
    config, project_root, config_source = found.config, found.project_root, found.source
    _load_plugin_modules([*config.get("plugins", []), *args.plugin])
    # Capture pristine defaults, then reset every check to them before applying
    # config. configure() only ever sets, never resets, so without this a check
    # configured by an earlier invocation in the same process would leak its
    # settings into this run. The cascading runner reuses the snapshot to reset
    # between regions.
    checks = get_all_checks()
    pristine = snapshot_defaults(checks)
    restore_defaults(checks=checks, snapshot=pristine)
    apply_check_config(config=config)

    if args.show_config:
        with reports.tolerate_closed_pipe():
            reports.print_config(
                config=config, source=config_source, project_root=project_root, extends=found.extends
            )
        return

    _run_and_report(
        args=args,
        config=config,
        scan_root=scan_root,
        project_root=project_root,
        targets=targets,
        pristine=pristine,
    )


def _run_baseline_command(*, args: argparse.Namespace) -> None:
    """Handle ``baseline write`` / ``baseline status`` over the whole project."""
    scan_root, targets = _resolve_targets(args.paths)
    # A baseline records the WHOLE project; a narrowed or sub-directory write
    # would regenerate from a partial run and silently prune everything out of
    # scope. Refuse it rather than corrupt the file.
    found = discover_config(start=scan_root, resolve_extends=_resolve_extends)
    config, project_root = found.config, found.project_root
    if targets is not None or scan_root.resolve() != project_root.resolve():
        raise UsageError(
            "'baseline' must run over the whole project root, without file "
            "targets or selection flags."
        )

    _load_plugin_modules(config.get("plugins", []))
    checks = get_all_checks()
    pristine = snapshot_defaults(checks)
    restore_defaults(checks=checks, snapshot=pristine)
    apply_check_config(config=config)

    baseline_path = _resolve_baseline_path(config=config, project_root=project_root)
    if baseline_path is None:
        baseline_path = project_root / "lanorme-baseline.json"

    collected = collect_results(
        config=config, scan_root=scan_root, project_root=project_root, targets=None,
        pristine=pristine,
        filters=Filters(
            single=None,
            select=_read_config_list(config.get("select")),
            ignore=_read_config_list(config.get("ignore")),
            exclude=_read_config_list(config.get("exclude")),
        ),
        resolve_single=_resolve_single,
    )
    if collected is None:
        print("No checks registered.")
        return
    results = collected.results

    with reports.tolerate_closed_pipe():
        if args.action == "write":
            baseline.write(results=results, project_root=project_root, baseline_path=baseline_path)
        else:
            baseline.print_status(
                results=results, project_root=project_root, baseline_path=baseline_path
            )


def main(argv: list[str] | None = None) -> None:
    """The console entry point: dispatch, and turn a usage error into exit 2."""
    configure_diagnostics()
    try:
        _dispatch(argv)
    except UsageError as error:
        logger.error("%s", error)
        sys.exit(2)


def _dispatch(argv: list[str] | None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(2)

    # `rules` needs the registry but no target.
    _load_builtin_checks()
    _load_entry_point_checks()

    if args.command == "rules":
        with reports.tolerate_closed_pipe():
            reference.print_rules(as_json=args.json)
        return

    if args.command == "rule":
        with reports.tolerate_closed_pipe():
            reference.print_rule_detail(code=args.code, as_json=args.json)
        return

    if args.command == "baseline":
        _run_baseline_command(args=args)
        return

    _run_check_command(args=args)


if __name__ == "__main__":
    main()
