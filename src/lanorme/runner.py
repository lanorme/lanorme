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

The confinement is a :class:`~lanorme.scan.Scan` handed to each check of a
pass (and active around the call), and each pass runs its own configured
copies of the registered checks, so no pass leaves state behind for the next.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import (
    Check,
    CheckResult,
    ResultAuditor,
    get_all_checks,
    get_registry,
    run_audit,
    run_check,
)
from lanorme.baseline import FindingKeys
from lanorme.discovery import find_narrower_scope
from lanorme.filters import Narrowing
from lanorme.presets import _resolve_extends
from lanorme.regions import (
    Config,
    Region,
    build_child_exclude_globs,
    combine_results,
    discover_regions,
    is_tree_scoped,
    compute_region_prefix,
)
from lanorme.scan import Scan
from lanorme.selectors import reject_unknown_selectors
from lanorme.source_lines import SourceLines


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
    """Where a run starts from and what it is confined to.

    *scan* covers the whole project from the run root: the exclude globs and
    the run's shared parse cache. *scope* is the subtree the scan asked for,
    relative to the run root (``""`` for the whole tree).
    """

    scan: Scan
    scope: str

    @property
    def run_root(self) -> Path:
        """The directory every check runs from."""
        return self.scan.root


def run_regions(
    *,
    regions: list[Region],
    root_checks: dict[str, Check],
    bounds: RunBounds,
    only: list[Check] | None = None,
) -> list[CheckResult]:
    """Run the checks within *bounds* under cascading config and merge the results.

    *root_checks* are the registered checks configured from the root config;
    *only* restricts the run to the given registered checks (a ``--check``
    selection); the default runs every registered check.

    Whole-tree checks run once from the run root over the whole tree, whatever
    the scope: a duplicate of a scanned file elsewhere in the project is still
    a duplicate. File-level checks run once per region, each pass confined by
    the discovery scope to the files that region directly governs (the nested
    regions below it are excluded) and to the scanned subtree, and configured
    with that region's merged settings. Every pass still runs from the run
    root, so a check sees ``tests/helpers.py`` and not ``helpers.py`` and its
    path-based exemptions hold.
    """
    names, reported = _select_checks(only=only)
    by_name = _run_tree_pass(checks={name: root_checks[name] for name in names}, bounds=bounds)
    for region in regions:
        passed = _run_region_pass(names=names, region=region, regions=regions, bounds=bounds)
        for name, result in passed.items():
            by_name[name] = combine_results(existing=by_name.get(name), addition=result)

    with bounds.scan.restrict(scope=bounds.scope).activate():
        for name in names:
            check = root_checks[name]
            if isinstance(check, ResultAuditor):
                by_name[name] = run_audit(check, results=by_name)
    return [by_name[name] for name in reported if name in by_name]


def _run_tree_pass(*, checks: dict[str, Check], bounds: RunBounds) -> dict[str, CheckResult]:
    """The whole-tree checks, once from the run root over the whole project."""
    return {
        name: run_check(check, scan=bounds.scan)
        for name, check in checks.items()
        if is_tree_scoped(check) and not isinstance(check, ResultAuditor)
    }


def _run_region_pass(
    *,
    names: list[str],
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
    checks = get_registry().build_configured(region.merged)
    region_scan = bounds.scan.restrict(scope=scope, excludes=nested)
    return {
        name: run_check(checks[name], scan=region_scan)
        for name in names
        if not is_tree_scoped(checks[name])
    }


def _select_checks(*, only: list[Check] | None) -> tuple[list[str], list[str]]:
    """The names of the checks to run and to report for a ``--check`` selection.

    A selected result auditor (``--check meta``) needs the other checks'
    results to judge, so the whole registry runs and only its result is
    reported; any other selection runs and reports just itself.
    """
    checks = get_all_checks()
    if only is None:
        return list(checks), list(checks)
    chosen = {id(check) for check in only}
    reported = [name for name, check in checks.items() if id(check) in chosen]
    if any(isinstance(check, ResultAuditor) for check in only):
        return list(checks), reported
    return reported, reported


@dataclass(frozen=True)
class RunConfigs:
    """The two configs a run reads.

    *project* is the project root's own config: the run keys (``select``,
    ``ignore``, ``exclude``, ``per-file-ignores``, ``source_root``,
    ``baseline``) and the whole-tree pass read it, whatever subtree is
    scanned. *scan* is the config in force at the scan root (the project's
    with every nested config down to it folded in), which the region passes
    start from. For a scan of the project root they are the same.
    """

    project: Config
    scan: Config


@dataclass(frozen=True)
class Filters:
    """The result-narrowing inputs a run applies (CLI value or config fallback)."""

    single: str | None
    select: list[str]
    ignore: list[str]
    exclude: list[str]


@dataclass(frozen=True)
class RunOutcome:
    """What a run found, and what it did around the findings, for the reports.

    ``selected`` names the checks the run was asked to report, in registry
    order: every check for a full run, the selection under ``--check``.
    ``dropped`` counts the findings each narrowing stage removed (``inline``,
    ``per_file`` and, once the CLI applies it, ``baseline`` are the ones a
    summary reports). ``keys`` is the run's shared finding-key cache, for the
    baseline and the fingerprints the JSON formats carry. ``opt_in_disabled``
    is how many selected checks ship off and were not enabled.
    """

    results: list[CheckResult]
    project_root: Path
    keys: FindingKeys
    selected: tuple[str, ...] = ()
    dropped: dict[str, int] = field(default_factory=dict)
    opt_in_disabled: int = 0
    baseline_configured: bool = False


def count_opt_in_disabled(*, checks: dict[str, Check], selected: Iterable[str]) -> int:
    """How many of the *selected* checks are opt-in and not enabled."""
    return sum(
        1
        for name in selected
        if name in checks and hasattr(checks[name], "enabled") and not checks[name].enabled
    )


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


def _run_checks(
    *,
    config: Config,
    configured: dict[str, Check],
    bounds: RunBounds,
    only: list[Check] | None,
) -> list[CheckResult]:
    """Discover the config regions within *bounds* and run the checks over them."""
    # The region walk honours the scope too, so only the regions on or under
    # the scanned subtree are found; the ones above it are already folded into
    # *config* by the walk-up discovery.
    with bounds.scan.restrict(scope=bounds.scope).activate():
        regions = discover_regions(
            scan_root=bounds.run_root,
            root_config=config,
            resolve_extends=_resolve_extends,
        )
    return run_regions(regions=regions, root_checks=configured, bounds=bounds, only=only)


def _read_per_file_ignores(*, config: Config, filters: Filters) -> dict[str, list[str]]:
    """The per-file-ignores table, once every selector the run was given names a rule."""
    per_file_ignores = parse_per_file_ignores(table=config.get("per-file-ignores", {}))
    reject_unknown_selectors(selectors=filters.select, origin="'select'")
    reject_unknown_selectors(selectors=filters.ignore, origin="'ignore'")
    for pattern, codes in per_file_ignores.items():
        reject_unknown_selectors(selectors=codes, origin=f"per-file-ignores entry '{pattern}'")
    return per_file_ignores


def collect_results(
    *,
    configs: RunConfigs,
    configured: dict[str, Check],
    scan_root: Path,
    project_root: Path,
    targets: list[Path] | None,
    filters: Filters,
    resolve_single: Callable[..., tuple[list[Check], list[str]]],
) -> RunOutcome | None:
    """Run the checks and narrow the results up to (and including) inline ignores.

    *configured* holds the registered checks configured from the project
    root's config; they run the whole-tree pass and judge the opt-in count,
    while each region's pass configures its own from *configs*. This is the
    shared spine of ``check``, ``baseline write`` and ``baseline status``: all
    three must see byte-identical findings through an identical path, or
    recorded anchors would not line up with checked ones. The baseline hook and
    promotion run after this, on the returned project-root-relative results.
    Returns ``None`` when no checks are registered.
    """
    config = configs.project
    per_file_ignores = _read_per_file_ignores(config=config, filters=filters)

    only: list[Check] | None = None
    implicit_select: list[str] = []
    if filters.single:
        only, implicit_select = resolve_single(selector=filters.single)
    run_root, scope = resolve_run_root(scan_root=scan_root, project_root=project_root)
    source_root = config.get("source_root")
    base = Scan(
        root=run_root,
        excludes=tuple(filters.exclude),
        source_root=source_root if isinstance(source_root, str) else "",
    )
    bounds = RunBounds(scan=base, scope=scope)
    results = _run_checks(config=configs.scan, configured=configured, bounds=bounds, only=only)
    if not results:
        return None

    keys = FindingKeys(SourceLines(project_root))
    narrowed = Narrowing(
        # A code-form ``--check`` (e.g. DRY-001) narrows to that code;
        # otherwise the filters' select (CLI then config) applies.
        select=implicit_select or filters.select,
        ignore=filters.ignore,
        exclude=filters.exclude,
        per_file_ignores=per_file_ignores,
        project_root=project_root,
        # A lone directory target narrows the report the same way named
        # targets do: the whole-tree checks saw the project, the report is
        # the subtree's.
        targets=targets if targets is not None or not scope else [scan_root],
        run_root=run_root,
        lines=keys.lines,
    ).apply(results)
    selected = tuple(_select_checks(only=only)[1])
    return RunOutcome(
        results=narrowed.results,
        project_root=project_root,
        keys=keys,
        selected=selected,
        dropped=narrowed.dropped,
        opt_in_disabled=count_opt_in_disabled(checks=configured, selected=selected),
        baseline_configured=bool(config.get("baseline")),
    )
