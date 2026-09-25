"""Run the registered checks over a project and narrow the results.

This is the spine shared by ``check``, ``baseline write`` and ``baseline
status``: all three must see byte-identical findings through an identical
path, or recorded baseline anchors would not line up with checked ones.

Every check runs from the project root, the directory whose config was
discovered. A scan of a subtree (``lanorme check tests``) confines the
discovery scope to that subtree instead of making it the root, so a check is
handed ``tests/helpers.py`` and its path-based exemptions hold, while the
tree-scoped checks (duplication, layers, coverage) still see the whole project:
the standard is the project's. Cascading per-directory config is handled here
too: each region's file-level pass is confined to the region's own files by
the same scope, under that region's merged settings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lanorme import (
    Check,
    CheckResult,
    ResultAuditor,
    get_all_checks,
    run_audit,
    run_check,
)
from lanorme.checkconfig import apply_check_config
from lanorme.discovery import find_narrower_scope, set_excludes, set_scope
from lanorme.filters import (
    _apply_excludes,
    _apply_filters,
    _apply_inline_ignores,
    _apply_per_file_ignores,
    _apply_target_filter,
)
from lanorme.presets import _resolve_extends
from lanorme.regions import (
    Config,
    Region,
    build_child_exclude_globs,
    combine_results,
    discover_regions,
    is_tree_scoped,
    reanchor_results,
    compute_region_prefix,
    restore_defaults,
)
from lanorme.selectors import reject_unknown_selectors
from lanorme.sources import clear_cache


def parse_per_file_ignores(*, table: object) -> dict[str, list[str]]:
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


@dataclass(frozen=True)
class RunBounds:
    """Where a run starts from, what it is confined to, and what it excludes.

    *scope* is the subtree the scan asked for, relative to *run_root* (``""``
    for the whole tree); *pristine* is the checks' default state, restored
    before each pass so one region's settings never leak into the next.
    """

    run_root: Path
    scope: str
    exclude: list[str]
    pristine: dict[str, object]


def run_regions(
    *,
    regions: list[Region],
    root_config: Config,
    bounds: RunBounds,
    only: list[Check] | None = None,
) -> list[CheckResult]:
    """Run the checks within *bounds* under cascading config and merge the results.

    *only* restricts the run to the given checks (a ``--check`` selection);
    the default runs every registered check.

    Whole-tree checks run once from the run root over the whole tree, whatever
    the scope: a duplicate of a scanned file elsewhere in the project is still
    a duplicate. File-level checks run once per region, each pass confined by
    the discovery scope to the files that region directly governs (the nested
    regions below it are excluded) and to the scanned subtree, and configured
    with that region's merged settings. Every pass still runs from the run
    root, so a check sees ``tests/helpers.py`` and not ``helpers.py`` and its
    path-based exemptions hold.
    """
    checks, reported = _select_checks(only=only)
    by_name = _run_tree_pass(checks=checks, root_config=root_config, bounds=bounds)
    for region in regions:
        passed = _run_region_pass(checks=checks, region=region, regions=regions, bounds=bounds)
        for name, result in passed.items():
            by_name[name] = combine_results(existing=by_name.get(name), addition=result)
    set_scope(bounds.scope)
    set_excludes(bounds.exclude)

    for name, check in checks.items():
        if isinstance(check, ResultAuditor):
            by_name[name] = run_audit(check, results=by_name)
    return [by_name[name] for name in reported if name in by_name]


def _configure_pass(
    *,
    config: Config,
    bounds: RunBounds,
    scope: str,
    nested: list[str] = (),
) -> None:
    """Reset the checks, apply *config*, and confine discovery for one pass."""
    restore_defaults(checks=get_all_checks(), snapshot=bounds.pristine)
    apply_check_config(config=config)
    set_excludes([*bounds.exclude, *nested])
    set_scope(scope)


def _run_tree_pass(
    *,
    checks: dict[str, Check],
    root_config: Config,
    bounds: RunBounds,
) -> dict[str, CheckResult]:
    """The whole-tree checks, once from the run root over the whole project."""
    _configure_pass(config=root_config, bounds=bounds, scope="")
    return {
        name: run_check(check, src_root=str(bounds.run_root))
        for name, check in checks.items()
        if is_tree_scoped(check) and not isinstance(check, ResultAuditor)
    }


def _run_region_pass(
    *,
    checks: dict[str, Check],
    region: Region,
    regions: list[Region],
    bounds: RunBounds,
) -> dict[str, CheckResult]:
    """The file-level checks over the files *region* governs inside the scanned subtree.

    Returns each check's result for this region alone; the caller folds it
    into the running total. A region outside the scanned subtree yields nothing.
    """
    prefix = compute_region_prefix(region=region, scan_root=bounds.run_root)
    scope = find_narrower_scope(outer=bounds.scope, inner=prefix)
    if scope is None:
        return {}
    nested = build_child_exclude_globs(region=region, regions=regions, scan_root=bounds.run_root)
    _configure_pass(config=region.merged, bounds=bounds, scope=scope, nested=nested)
    return {
        name: run_check(check, src_root=str(bounds.run_root))
        for name, check in checks.items()
        if not is_tree_scoped(check)
    }


def _select_checks(*, only: list[Check] | None) -> tuple[dict[str, Check], list[str]]:
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


@dataclass(frozen=True)
class Filters:
    """The result-narrowing inputs a run applies (CLI value or config fallback)."""

    single: str | None
    select: list[str]
    ignore: list[str]
    exclude: list[str]


@dataclass(frozen=True)
class CollectedResults:
    """The filtered results of a run and how many findings the silencers dropped.

    ``selected`` names the checks the run was asked to report, in registry
    order: every check for a full run, the selection under ``--check``.
    """

    results: list[CheckResult]
    selected: tuple[str, ...] = ()
    suppressed_per_file: int = 0
    suppressed_inline: int = 0


def resolve_run_root(*, scan_root: Path, project_root: Path) -> tuple[Path, str]:
    """Where the checks run from, and the subtree the scan is confined to.

    A scan root inside the project runs from the project root with the scope
    set to the subtree, so the checks see project-relative paths and the
    tree-scoped ones see the whole project. A scan root that is the project
    root, or lies outside it, runs from itself with no scope, as before.
    """
    try:
        relative = scan_root.resolve().relative_to(project_root.resolve())
    except ValueError:
        return scan_root, ""
    if relative == Path():
        return scan_root, ""
    return project_root, relative.as_posix()


def count_findings(results: list[CheckResult]) -> int:
    return sum(len(r.violations) + len(r.warnings) for r in results)


def collect_results(
    *,
    config: dict[str, object],
    scan_root: Path,
    project_root: Path,
    targets: list[Path] | None,
    pristine: dict[str, object],
    filters: Filters,
    resolve_single: Callable[..., tuple[list[Check], list[str]]],
) -> CollectedResults | None:
    """Run the checks and apply every filter up to (and including) inline ignores.

    This is the shared spine of ``check``, ``baseline write`` and ``baseline
    status``: all three must see byte-identical findings through an identical
    path, or recorded anchors would not line up with checked ones. The baseline
    hook and promotion run after this, on the returned project-root-relative
    results. Returns ``None`` when no checks are registered.
    """
    per_file_ignores = parse_per_file_ignores(table=config.get("per-file-ignores", {}))
    reject_unknown_selectors(selectors=filters.select, origin="'select'")
    reject_unknown_selectors(selectors=filters.ignore, origin="'ignore'")
    for pattern, codes in per_file_ignores.items():
        reject_unknown_selectors(selectors=codes, origin=f"per-file-ignores entry '{pattern}'")
    set_excludes(filters.exclude)
    clear_cache()

    only: list[Check] | None = None
    implicit_select: list[str] = []
    if filters.single:
        only, implicit_select = resolve_single(selector=filters.single)
    run_root, scope = resolve_run_root(scan_root=scan_root, project_root=project_root)
    set_scope(scope)
    try:
        # The region walk honours the scope too, so only the regions on or
        # under the scanned subtree are found; the ones above it are already
        # folded into *config* by the walk-up discovery.
        regions = discover_regions(
            scan_root=run_root,
            root_config=config,
            resolve_extends=_resolve_extends,
        )
        results = run_regions(
            regions=regions,
            root_config=config,
            bounds=RunBounds(
                run_root=run_root,
                scope=scope,
                exclude=filters.exclude,
                pristine=pristine,
            ),
            only=only,
        )
    finally:
        set_scope("")

    if not results:
        return None

    # A code-form ``--check`` (e.g. DRY-001) narrows to that code; otherwise the
    # filters' select (CLI then config) applies.
    effective_select = implicit_select or filters.select
    # A lone directory target narrows the report the same way named targets
    # do: the whole-tree checks saw the project, the report is the subtree's.
    scoped_targets = targets if targets is not None or not scope else [scan_root]
    results = _apply_target_filter(results=results, run_root=run_root, targets=scoped_targets)
    # The target filter works in run-root-relative paths; everything after it
    # (config globs, inline-ignore source lookup, display) works in project-root-relative.
    results = reanchor_results(results=results, from_root=run_root, to_root=project_root)
    results = _apply_filters(results=results, select=effective_select, ignore=filters.ignore)
    results = _apply_excludes(results=results, exclude=filters.exclude)
    before = count_findings(results)
    results = _apply_per_file_ignores(results=results, table=per_file_ignores)
    per_file = before - count_findings(results)
    before = count_findings(results)
    results = _apply_inline_ignores(results=results, project_root=project_root)
    return CollectedResults(
        results=results,
        selected=tuple(_select_checks(only=only)[1]),
        suppressed_per_file=per_file,
        suppressed_inline=before - count_findings(results),
    )
