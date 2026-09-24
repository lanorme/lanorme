"""Run the registered checks over a scan root and narrow the results.

This is the spine shared by ``check``, ``baseline write`` and ``baseline
status``: all three must see byte-identical findings through an identical
path, or recorded baseline anchors would not line up with checked ones.
Cascading per-directory config is handled here too: each region's file-level
pass runs from the scan root, confined to the region's own files by the
discovery scope, under that region's merged settings.
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
    run_all,
    run_audit,
    run_check,
)
from lanorme.checkconfig import apply_check_config
from lanorme.discovery import set_excludes, set_scope
from lanorme.filtering import (
    _apply_excludes,
    _apply_filters,
    _apply_inline_ignores,
    _apply_per_file_ignores,
    _apply_target_filter,
)
from lanorme.presets import _resolve_extends
from lanorme.regions import (
    Region,
    child_exclude_globs,
    combine_results,
    discover_regions,
    is_tree_scoped,
    reanchor_results,
    region_prefix,
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


def run_regions(
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

    File-level checks run once per region, each pass confined by the discovery
    scope to the files that region directly governs (the nested regions below
    it are excluded) and configured with that region's merged settings. Every
    pass still runs from the scan root, so a check sees ``tests/helpers.py``
    and not ``helpers.py`` and its path-based exemptions hold. Whole-tree
    checks run once at the scan root under the root config.
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
        nested = child_exclude_globs(region=region, regions=regions, scan_root=scan_root)
        set_excludes([*exclude, *nested])
        set_scope(region_prefix(region=region, scan_root=scan_root))
        for name, check in checks.items():
            if not is_tree_scoped(check):
                result = run_check(check, src_root=str(scan_root))
                by_name[name] = combine_results(existing=by_name.get(name), addition=result)
    set_scope("")
    set_excludes(exclude)

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


@dataclass(frozen=True)
class Filters:
    """The result-narrowing inputs a run applies (CLI value or config fallback)."""

    single: str | None
    select: list[str]
    ignore: list[str]
    exclude: list[str]


@dataclass(frozen=True)
class Collected:
    """The filtered results of a run and how many findings the silencers dropped."""

    results: list[CheckResult]
    suppressed_per_file: int = 0
    suppressed_inline: int = 0


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
) -> Collected | None:
    """Run the checks and apply every filter up to (and including) inline ignores.

    This is the shared spine of ``check``, ``baseline write`` and ``baseline
    status``: all three must see byte-identical findings through an identical
    path, or recorded anchors would not line up with checked ones. The baseline
    hook and promotion run after this, on the returned project-root-relative
    results. Returns ``None`` when no checks are registered.
    """
    src_root = str(scan_root)
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
    regions = discover_regions(
        scan_root=scan_root, root_config=config, resolve_extends=_resolve_extends
    )
    if len(regions) > 1:
        results = run_regions(
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
    before = count_findings(results)
    results = _apply_per_file_ignores(results=results, table=per_file_ignores)
    per_file = before - count_findings(results)
    before = count_findings(results)
    results = _apply_inline_ignores(results=results, project_root=project_root)
    return Collected(
        results=results, suppressed_per_file=per_file, suppressed_inline=before - count_findings(results)
    )
