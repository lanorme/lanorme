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
import os
import pkgutil
import re
import sys
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path

import lanorme.checks
from lanorme import baseline, reporting
from lanorme import (
    Check,
    CheckResult,
    ResultAuditor,
    Status,
    __version__,
    get_all_checks,
    get_check,
    rule_code,
    run_all,
    run_audit,
    run_check,
)
from lanorme.checkconfig import apply_check_config
from lanorme.discovery import set_excludes
from lanorme.sources import clear_cache
from lanorme.filtering import (
    _apply_excludes,
    _apply_filters,
    _apply_inline_ignores,
    _apply_per_file_ignores,
    _apply_promotions,
    _apply_target_filter,
    note_excluded_targets,
)
from lanorme.presets import _resolve_extends
from lanorme.selectors import checks_for_selector, reject_unknown_selectors
from lanorme.regions import (
    Region,
    child_exclude_globs,
    combine_results,
    dedicated_config_file,
    discover_regions,
    is_tree_scoped,
    read_toml,
    reanchor_results,
    restore_defaults,
    snapshot_defaults,
)

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


def _discover_config(*, start: Path) -> tuple[dict, Path, str | None]:
    """Walk up from *start* for lanorme config. Return (config, project_root, source)."""
    start = start.resolve()
    search_dir = start if start.is_dir() else start.parent
    for directory in (search_dir, *search_dir.parents):
        dedicated = dedicated_config_file(directory)
        if dedicated is not None:
            return read_toml(dedicated), directory, str(dedicated)

        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            tool_cfg = read_toml(pyproject).get("tool", {}).get("lanorme")
            if tool_cfg is not None:
                return tool_cfg, directory, f"{pyproject} [tool.lanorme]"

    return {}, search_dir, None


# --------------------------------------------------------------------------- #
# Rule-code filtering (select / ignore)
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
        return [by_name], []

    matched = checks_for_selector(selector=selector)
    if matched:
        return matched, [selector.upper()]

    names = ", ".join(sorted(get_all_checks())) or "(none)"
    print(
        f"ERROR: '{selector}' is not a known check name, rule code, or category.\n"
        f"  Checks: {names}\n"
        f"  Run 'lanorme rules' to see every rule code and category.",
        file=sys.stderr,
    )
    sys.exit(2)


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
            print(f"ERROR: path '{path}' does not exist.", file=sys.stderr)
            sys.exit(2)

    if len(resolved) == 1 and resolved[0].is_dir():
        return resolved[0], None

    try:
        common = Path(os.path.commonpath([str(p.resolve()) for p in resolved]))
    except ValueError:
        print("ERROR: cannot check paths located on different drives.", file=sys.stderr)
        sys.exit(2)
    return (common.parent if common.is_file() else common), resolved


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()] if value else []


def _config_list(value: object) -> list[str]:
    """Normalise a config selector value to a list of strings.

    Accepts a list (``promote = ["TYPE-004"]``) or a bare string
    (``promote = "ALL"``); anything else yields ``[]``. Whitespace and case are
    handled downstream by ``_matches``, matching the CLI ``_csv`` path.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _parse_per_file_ignores(*, table: object) -> dict[str, list[str]]:
    """Normalise the ``[tool.lanorme.per-file-ignores]`` TOML table to {glob: [codes]}."""
    if not isinstance(table, dict):
        return {}
    out: dict[str, list[str]] = {}
    for pattern, codes in table.items():
        if not isinstance(pattern, str) or not isinstance(codes, list):
            continue
        normalised = [c for c in codes if isinstance(c, str)]
        if normalised:
            out[pattern] = normalised
    return out


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

    check = sub.add_parser("check", help="Run checks against one or more paths.")
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
        choices=["concise", "full", "json", "ndjson", "github"],
        default=None,
        help=(
            "Output format (default: concise). 'concise' shows only checks with findings plus a "
            "summary; 'full' shows every check; 'json' is one object per check; 'ndjson' is one "
            "finding per line; 'github' emits workflow commands (auto-detected when GITHUB_ACTIONS=true)."
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

    sub.add_parser("rules", help="List all registered rules and exit.")

    rule = sub.add_parser("rule", help="Print the reference section for a single rule code.")
    rule.add_argument("code", help="The rule code to look up (e.g. CMT-001, SQL-001).")

    return parser


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def _run_regions(
    *,
    regions: list[Region],
    root_config: dict[str, object],
    scan_root: Path,
    exclude: list[str],
    pristine: dict[str, object],
    only: list[Check] | None = None,
) -> list[CheckResult]:
    """Run the checks under cascading per-directory config and merge the results.

    *only* restricts the run to the given checks (a ``--check`` selection); the
    default runs every registered check.

    File-level checks run once per region, each pass scoped to the files that
    region directly governs (the nested regions below it are excluded) and
    configured with that region's merged settings; their findings are re-anchored
    back to the scan root and folded together. Whole-tree checks run once at the
    scan root under the root config. Results land in scan-root coordinates, the
    same as a single-region run, so the downstream pipeline is unchanged.
    """
    checks, reported = _selected(only=only)
    by_name: dict[str, CheckResult] = {}

    restore_defaults(checks=get_all_checks(), snapshot=pristine)
    apply_check_config(config=root_config)
    set_excludes(exclude)
    for name, check in checks.items():
        if is_tree_scoped(check) and not isinstance(check, ResultAuditor):
            by_name[name] = run_check(check, src_root=str(scan_root))

    for region in regions:
        restore_defaults(checks=get_all_checks(), snapshot=pristine)
        apply_check_config(config=region.merged)
        region_excludes = child_exclude_globs(region=region, regions=regions)
        # The user's excludes are written relative to the scan root, so they
        # prune correctly only in the root region's walk (rooted there). Nested
        # regions walk from their own directory, where those globs would not
        # line up, so their user excludes are left to the post-filter, which
        # works in project-root coordinates and still drops the findings.
        if region.directory == scan_root.resolve():
            region_excludes = list(exclude) + region_excludes
        set_excludes(region_excludes)
        for name, result in _run_file_checks(checks=checks, region=region, scan_root=scan_root):
            by_name[name] = combine_results(existing=by_name.get(name), addition=result)

    for name, check in checks.items():
        if isinstance(check, ResultAuditor):
            by_name[name] = run_audit(check, results=by_name)
    return [by_name[name] for name in reported if name in by_name]


def _selected(*, only: list[Check] | None) -> tuple[dict[str, Check], list[str]]:
    """The checks to run and the names to report for a ``--check`` selection.

    A selected result auditor (``--check meta``) needs the other checks'
    results to judge, so the whole registry runs and only its result is
    reported; any other selection runs and reports just itself.
    """
    checks = get_all_checks()
    if only is None:
        return checks, list(checks)
    chosen = {id(check) for check in only}
    reported = [name for name, check in checks.items() if id(check) in chosen]
    if any(isinstance(check, ResultAuditor) for check in only):
        return checks, reported
    return {name: checks[name] for name in reported}, reported


def _run_file_checks(
    *, checks: dict[str, Check], region: Region, scan_root: Path
) -> list[tuple[str, CheckResult]]:
    """One region's pass of the file-level checks, re-anchored to the scan root."""
    passed: list[tuple[str, CheckResult]] = []
    for name, check in checks.items():
        if is_tree_scoped(check):
            continue
        result = run_check(check, src_root=str(region.directory))
        (result,) = reanchor_results(results=[result], from_root=region.directory, to_root=scan_root)
        passed.append((name, result))
    return passed


@dataclass(frozen=True)
class _Filters:
    """The result-narrowing inputs a run applies (CLI value or config fallback)."""

    single: str | None
    select: list[str]
    ignore: list[str]
    exclude: list[str]


def _collect_results(
    *,
    config: dict[str, object],
    scan_root: Path,
    project_root: Path,
    targets: list[Path] | None,
    pristine: dict[str, object],
    filters: _Filters,
) -> list[CheckResult] | None:
    """Run the checks and apply every filter up to (and including) inline ignores.

    This is the shared spine of ``check``, ``baseline write`` and ``baseline
    status``: all three must see byte-identical findings through an identical
    path, or recorded anchors would not line up with checked ones. The baseline
    hook and promotion run after this, on the returned project-root-relative
    results. Returns ``None`` when no checks are registered.
    """
    src_root = str(scan_root)
    per_file_ignores = _parse_per_file_ignores(table=config.get("per-file-ignores", {}))
    reject_unknown_selectors(selectors=filters.select, origin="'select'")
    reject_unknown_selectors(selectors=filters.ignore, origin="'ignore'")
    for pattern, codes in per_file_ignores.items():
        reject_unknown_selectors(selectors=codes, origin=f"per-file-ignores entry '{pattern}'")
    set_excludes(filters.exclude)
    clear_cache()

    only: list[Check] | None = None
    implicit_select: list[str] = []
    if filters.single:
        only, implicit_select = _resolve_single(selector=filters.single)
    regions = discover_regions(
        scan_root=scan_root, root_config=config, resolve_extends=_resolve_extends
    )
    if len(regions) > 1:
        results = _run_regions(
            regions=regions, root_config=config, scan_root=scan_root,
            exclude=filters.exclude, pristine=pristine, only=only,
        )
    elif only is not None:
        results = [run_check(check, src_root=src_root) for check in only]
    else:
        results = run_all(src_root=src_root)

    if not results:
        return None

    # A code-form ``--check`` (e.g. DRY-001) narrows to that code; otherwise the
    # filters' select (CLI then config) applies.
    effective_select = implicit_select or filters.select
    results = _apply_target_filter(results=results, scan_root=scan_root, targets=targets)
    # The target filter works in scan-root-relative paths; everything after it
    # (config globs, inline-ignore source lookup, display) works in project-root-relative.
    results = reanchor_results(results=results, from_root=scan_root, to_root=project_root)
    results = _apply_filters(results=results, select=effective_select, ignore=filters.ignore)
    results = _apply_excludes(results=results, exclude=filters.exclude)
    results = _apply_per_file_ignores(results=results, table=per_file_ignores)
    return _apply_inline_ignores(results=results, project_root=project_root)


def _baseline_path(*, config: dict[str, object], project_root: Path) -> Path | None:
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
    ignore = _csv(args.ignore) or _config_list(config.get("ignore"))
    exclude = _csv(args.exclude) or _config_list(config.get("exclude"))
    promote = _csv(args.promote) or _config_list(config.get("promote"))
    select = _csv(args.select) or _config_list(config.get("select"))
    output_format = reporting.resolve_output_format(explicit=args.output_format, as_json=args.json)
    reject_unknown_selectors(selectors=promote, origin="'promote'")

    results = _collect_results(
        config=config, scan_root=scan_root, project_root=project_root, targets=targets,
        pristine=pristine,
        filters=_Filters(single=args.single, select=select, ignore=ignore, exclude=exclude),
    )
    if results is None:
        print("No checks registered.")
        return
    note_excluded_targets(targets=targets, project_root=project_root, exclude=exclude)

    drifted: list[tuple[str, str]] = []
    if not args.no_baseline:
        results, drifted = _apply_baseline(results=results, config=config, project_root=project_root)

    results = _apply_promotions(results=results, promote=promote)
    failed = any(r.status == Status.FAIL for r in results)
    with reporting.tolerate_closed_pipe():
        reporting.emit(results=results, output_format=output_format)
        reporting.print_baseline_drift(drifted=drifted, output_format=output_format)
    if failed:
        sys.exit(1)


def _apply_baseline(
    *, results: list[CheckResult], config: dict[str, object], project_root: Path
) -> tuple[list[CheckResult], list[tuple[str, str]]]:
    """Suppress the configured baseline's findings; return the survivors and the drift."""
    baseline_path = _baseline_path(config=config, project_root=project_root)
    if baseline_path is None:
        return results, []
    if not baseline_path.exists():
        print(
            f"ERROR: baseline file '{baseline_path}' does not exist. "
            "Run 'lanorme baseline write' first.",
            file=sys.stderr,
        )
        sys.exit(2)
    # Drift reads the raw findings: it has to see what the baseline did
    # match to tell a moved anchor from debt that is genuinely new.
    drifted = baseline.drifted_codes(
        results=results, project_root=project_root, baseline_path=baseline_path
    )
    suppressed = baseline.suppress(
        results=results, project_root=project_root, baseline_path=baseline_path
    )
    return suppressed, drifted


def _run_check_command(*, args: argparse.Namespace) -> None:
    """Handle the ``check`` subcommand: discover config, then report or run."""
    scan_root, targets = _resolve_targets(args.paths)

    config, project_root, config_source = _discover_config(start=scan_root)
    extends = config.get("extends")
    config = _resolve_extends(config=config, project_root=project_root)
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
        with reporting.tolerate_closed_pipe():
            reporting.print_config(
                config=config, source=config_source, project_root=project_root, extends=extends
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
    config, project_root, _source = _discover_config(start=scan_root)
    if targets is not None or scan_root.resolve() != project_root.resolve():
        print(
            "ERROR: 'baseline' must run over the whole project root, without file "
            "targets or selection flags.",
            file=sys.stderr,
        )
        sys.exit(2)

    config = _resolve_extends(config=config, project_root=project_root)
    _load_plugin_modules(config.get("plugins", []))
    checks = get_all_checks()
    pristine = snapshot_defaults(checks)
    restore_defaults(checks=checks, snapshot=pristine)
    apply_check_config(config=config)

    baseline_path = _baseline_path(config=config, project_root=project_root)
    if baseline_path is None:
        baseline_path = project_root / "lanorme-baseline.json"

    results = _collect_results(
        config=config, scan_root=scan_root, project_root=project_root, targets=None,
        pristine=pristine,
        filters=_Filters(
            single=None,
            select=_config_list(config.get("select")),
            ignore=_config_list(config.get("ignore")),
            exclude=_config_list(config.get("exclude")),
        ),
    )
    if results is None:
        print("No checks registered.")
        return

    if args.action == "write":
        baseline.write(results=results, project_root=project_root, baseline_path=baseline_path)
    else:
        baseline.print_status(
            results=results, project_root=project_root, baseline_path=baseline_path
        )


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(2)

    # `rules` needs the registry but no target.
    _load_builtin_checks()
    _load_entry_point_checks()

    if args.command == "rules":
        with reporting.tolerate_closed_pipe():
            reporting.print_rules()
        return

    if args.command == "rule":
        with reporting.tolerate_closed_pipe():
            status = reporting.print_rule_detail(code=args.code)
        sys.exit(status)

    if args.command == "baseline":
        _run_baseline_command(args=args)
        return

    _run_check_command(args=args)


if __name__ == "__main__":
    main()
