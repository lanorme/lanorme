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
from dataclasses import replace
from functools import partial
from importlib.metadata import entry_points
from pathlib import Path

import lanorme.checks
from lanorme import (
    Check,
    Status,
    __version__,
    baseline,
    get_all_checks,
    get_check,
    get_registry,
    reference,
    reports,
)
from lanorme.diagnostics import configure_diagnostics
from lanorme.errors import UsageError
from lanorme.filters import apply_promotions, count_findings, note_excluded_targets
from lanorme.presets import _resolve_extends
from lanorme.regions import DiscoveredConfig, discover_config, reject_unknown_top_level_keys
from lanorme.runner import Filters, RunConfigs, RunOutcome, collect_results
from lanorme.selectors import checks_for_selector, reject_unknown_selectors

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


def _resolve_single(
    *,
    selector: str,
    configured: dict[str, Check],
) -> tuple[list[Check], list[str]]:
    """The check(s) named or coded by *selector*, and the implicit code narrowing.

    Resolution is name-first (an exact check name like ``duplication``), then by
    rule code or category (``DRY-001`` / ``SIZE``, case-insensitive). When a code
    or category is given, it is returned as an implicit selector so the output is
    narrowed to that code, and only the owning check(s) run. Exits 2 if unknown.
    The registered checks are returned; *configured* (the same checks under the
    run's config) says whether the selection is enabled.
    """
    by_name = get_check(selector)
    if by_name is not None:
        _note_disabled_selection(only=[by_name], configured=configured)
        return [by_name], []

    matched = checks_for_selector(selector=selector)
    if matched:
        _note_disabled_selection(only=matched, configured=configured)
        return matched, [selector.upper()]

    names = ", ".join(sorted(get_all_checks())) or "(none)"
    raise UsageError(
        f"'{selector}' is not a known check name, rule code, or category.\n"
        f"  Checks: {names}\n"
        f"  Run 'lanorme rules' to see every rule code and category.",
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
    handled downstream by ``is_code_matched``, matching the CLI ``_split_csv`` path.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def _build_check_parser(*, sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """The ``check`` subcommand: targets and rule selection."""
    check = sub.add_parser(
        "check",
        help="Run checks against one or more paths.",
        epilog=(
            "Silence a finding on its line with '# lanorme: ignore[CODE]' (or '# noqa: CODE'), "
            'a file pattern with [tool.lanorme.per-file-ignores] ("tests/*" = ["CODE"]), '
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
    check.add_argument(
        "--select",
        default=None,
        help="Comma-separated rule codes/categories to run.",
    )
    check.add_argument(
        "--ignore",
        default=None,
        help="Comma-separated rule codes/categories to skip.",
    )
    check.add_argument(
        "--exclude",
        default=None,
        help="Comma-separated file-path globs to exclude.",
    )
    check.add_argument(
        "--promote",
        default=None,
        help="Comma-separated rule codes/categories whose warnings become build-failing errors (or ALL).",
    )
    _add_check_output_arguments(check=check)
    return check


def _add_check_output_arguments(*, check: argparse.ArgumentParser) -> None:
    """The ``check`` flags that shape what is printed, not what is checked."""
    check.add_argument(
        "--show-config",
        action="store_true",
        help="Print the discovered config and effective per-check settings, then exit.",
    )
    check.add_argument(
        "--plugin",
        action="append",
        default=[],
        help="Plugin module to load (repeatable).",
    )
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lanorme",
        description="La norme — architecture & code-quality linter for Python.",
    )
    parser.add_argument("--version", action="version", version=f"lanorme {__version__}")
    sub = parser.add_subparsers(dest="command")

    _build_check_parser(sub=sub)

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
    rule.add_argument(
        "--json",
        action="store_true",
        help="Emit the declaration and section as JSON.",
    )

    return parser


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def _note_disabled_selection(*, only: list[Check], configured: dict[str, Check]) -> None:
    """Say so when every selected check is opt-in and not enabled: the run would be silent."""
    effective = [configured.get(c.name, c) for c in only]
    disabled = [c.name for c in effective if hasattr(c, "enabled") and not getattr(c, "enabled")]
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
    found: DiscoveredConfig,
    scan_root: Path,
    targets: list[Path] | None,
    configured: dict[str, Check],
) -> None:
    """Run the selected checks, apply the filters, print, and set the exit code.

    *found* is the config in force at the scan root and *configured* the
    checks configured from it. The run keys and the whole-tree pass read the
    project root's own config instead, so ``check sub`` applies the same
    standard as ``check .`` and a nested config governs only its region.
    """
    project_root = found.project_root
    config = _read_project_config(found=found, scan_root=scan_root)
    root_checks = configured if config is found.config else get_registry().build_configured(config)
    ignore = _split_csv(args.ignore) or _read_config_list(config.get("ignore"))
    exclude = _split_csv(args.exclude) or _read_config_list(config.get("exclude"))
    promote = _split_csv(args.promote) or _read_config_list(config.get("promote"))
    select = _split_csv(args.select) or _read_config_list(config.get("select"))
    output_format = reports.resolve_output_format(explicit=args.output_format, as_json=args.json)
    reject_unknown_selectors(selectors=promote, origin="'promote'")

    outcome = collect_results(
        configs=RunConfigs(project=config, scan=found.config),
        configured=root_checks,
        scan_root=scan_root,
        project_root=project_root,
        targets=targets,
        filters=Filters(single=args.single, select=select, ignore=ignore, exclude=exclude),
        resolve_single=partial(_resolve_single, configured=configured),
    )
    if outcome is None:
        print("No checks registered.")
        return
    note_excluded_targets(targets=targets, project_root=project_root, exclude=exclude)

    drifted: list[tuple[str, str]] = []
    if not args.no_baseline:
        outcome, drifted = _apply_baseline(outcome=outcome, config=config)
    outcome = replace(outcome, results=apply_promotions(results=outcome.results, promote=promote))
    failed = any(r.status == Status.FAIL for r in outcome.results)
    with reports.tolerate_closed_pipe():
        reports.emit(outcome=outcome, output_format=output_format)
        reports.print_baseline_drift(drifted=drifted, output_format=output_format)
    if failed:
        sys.exit(1)


def _apply_baseline(
    *,
    outcome: RunOutcome,
    config: dict[str, object],
) -> tuple[RunOutcome, list[tuple[str, str]]]:
    """Suppress the configured baseline's findings; return the narrowed run and the drift.

    The baseline file is read once; drift is judged on the raw findings, since
    it has to see what the baseline did match to tell a moved anchor from debt
    that is genuinely new.
    """
    baseline_path = _resolve_baseline_path(config=config, project_root=outcome.project_root)
    if baseline_path is None:
        return outcome, []
    if not baseline_path.exists():
        raise UsageError(
            f"baseline file '{baseline_path}' does not exist. Run 'lanorme baseline write' first.",
        )
    recorded = baseline.Baseline.load(baseline_path, keys=outcome.keys)
    drifted = recorded.find_drift(outcome.results)
    survivors = recorded.suppress(outcome.results)
    dropped = count_findings(outcome.results) - count_findings(survivors)
    narrowed = replace(
        outcome,
        results=survivors,
        dropped={**outcome.dropped, "baseline": dropped},
    )
    return narrowed, drifted


def _read_project_config(*, found: DiscoveredConfig, scan_root: Path) -> dict[str, object]:
    """The project root's own config, which the run keys and whole-tree checks read.

    *found* is the config in force at *scan_root*: the project root's with
    every nested config down to the scan root folded in. For a scan of the
    project root the two are the same object; for a subtree scan the nested
    configs govern only the region passes, never the run.
    """
    if scan_root.resolve() == found.project_root.resolve():
        return found.config
    return discover_config(start=found.project_root, resolve_extends=_resolve_extends).config


def _run_check_command(*, args: argparse.Namespace) -> None:
    """Handle the ``check`` subcommand: discover config, then report or run."""
    scan_root, targets = _resolve_targets(args.paths)

    found = discover_config(start=scan_root, resolve_extends=_resolve_extends)
    config = found.config
    _load_plugin_modules([*config.get("plugins", []), *args.plugin])
    reject_unknown_top_level_keys(config=config, origin="[tool.lanorme]")
    # The registered checks are templates; the run works on configured copies,
    # so nothing an earlier run in this process configured can leak into it.
    configured = get_registry().build_configured(config)

    if args.show_config:
        with reports.tolerate_closed_pipe():
            reports.print_config(checks=configured, found=found)
        return

    _run_and_report(
        args=args,
        found=found,
        scan_root=scan_root,
        targets=targets,
        configured=configured,
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
            "targets or selection flags.",
        )

    _load_plugin_modules(config.get("plugins", []))
    reject_unknown_top_level_keys(config=config, origin="[tool.lanorme]")
    configured = get_registry().build_configured(config)

    baseline_path = _resolve_baseline_path(config=config, project_root=project_root)
    if baseline_path is None:
        baseline_path = project_root / "lanorme-baseline.json"

    outcome = collect_results(
        configs=RunConfigs(project=config, scan=config),
        configured=configured,
        scan_root=scan_root,
        project_root=project_root,
        targets=None,
        filters=Filters(
            single=None,
            select=_read_config_list(config.get("select")),
            ignore=_read_config_list(config.get("ignore")),
            exclude=_read_config_list(config.get("exclude")),
        ),
        resolve_single=partial(_resolve_single, configured=configured),
    )
    if outcome is None:
        print("No checks registered.")
        return
    results, keys = outcome.results, outcome.keys

    with reports.tolerate_closed_pipe():
        if args.action == "write":
            baseline.write(
                results=results,
                project_root=project_root,
                baseline_path=baseline_path,
                keys=keys,
            )
        else:
            baseline.print_status(
                results=results,
                project_root=project_root,
                baseline_path=baseline_path,
                keys=keys,
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
