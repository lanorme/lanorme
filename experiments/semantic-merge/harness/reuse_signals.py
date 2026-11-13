"""Common Reuse evidence for a package: do its members share importers and import each other?

The Common Reuse Principle groups classes that are reused together: a client
that depends on one member of a package should depend on all of it
[@martin2002agile, ch. 20]. Ousterhout's criteria for merging are close:
modules that share information, or that are used together, belong together
[@ousterhout2018philosophy, ch. 9]. This module measures both on the import
graph the rule already builds:

- **Used together.** For every production module outside the package that
  imports a member, the share of the members it imports; and how many of
  those importers use two or more members.
- **Shared importers.** The mean pairwise Jaccard similarity of the members'
  importer sets.
- **Internal coupling.** The share of member pairs joined by an import, and the
  share of members in the largest group connected by such imports.

An import of the package itself (``from pkg import Name``) reaches a member
through the package's ``__init__.py``. It is attributed to the member that
``__init__.py`` re-exports the name from, or to ``__init__.py`` when that file
holds code and defines the name itself. The import graph also records every
``__init__.py`` Python runs on the way to a submodule; that is not a use of
the package file, and is not counted as one.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from lanorme.module_graph import PACKAGE_FILE, ModuleGraph
from lanorme.paths import is_test_file
from lanorme.sources import Module


@dataclass(frozen=True)
class PackageView:
    """What the reuse signals read: a package, its members and every module of its tree."""

    package: str
    members: tuple[Module, ...]
    modules: dict[str, Module]
    graph: ModuleGraph


def _list_member_stems(*, view: PackageView) -> dict[str, str]:
    """Each member's path by its module name."""
    return {
        member.relative.rsplit("/", 1)[-1].removesuffix(".py"): member.relative
        for member in view.members
    }


def collect_reexports(*, view: PackageView) -> dict[str, str]:
    """Each name the package's ``__init__.py`` re-exports, mapped to the member it comes from."""
    package_file = view.modules.get(f"{view.package}/{PACKAGE_FILE}")
    if package_file is None:
        return {}
    by_stem = _list_member_stems(view=view)
    exported: dict[str, str] = {}
    for imported in (found for found in package_file.imports if found.is_from):
        source = by_stem.get(imported.module.rsplit(".", 1)[-1])
        for alias in imported.aliases:
            member = source if source is not None or imported.module else by_stem.get(alias.name)
            if member is not None:
                exported[alias.asname or alias.name] = member
    return exported


def _list_package_names(*, view: PackageView, importer: Module) -> list[str]:
    """Every name *importer* imports from the package itself (``from pkg import Name``)."""
    name = view.package.rsplit("/", 1)[-1]
    return [
        alias.name
        for imported in importer.imports
        if imported.is_from
        and (
            imported.module.rsplit(".", 1)[-1] == name
            or (imported.level > 0 and not imported.module)
        )
        for alias in imported.aliases
    ]


def _collect_facade_uses(*, view: PackageView, importer: Module) -> set[str]:
    """The members *importer* reaches by naming the package rather than a member.

    A re-exported name is attributed to the member it comes from. Any other
    name that is not a submodule is defined in ``__init__.py`` itself, and
    counts as a use of it when ``__init__.py`` holds code.
    """
    exported = collect_reexports(view=view)
    stems = _list_member_stems(view=view)
    package_file = f"{view.package}/{PACKAGE_FILE}"
    holds_code = package_file in stems.values()
    used: set[str] = set()
    for name in _list_package_names(view=view, importer=importer):
        if name in exported:
            used.add(exported[name])
        elif holds_code and name not in stems:
            used.add(package_file)
    return used


def _is_outside_production(*, importer: str, view: PackageView) -> bool:
    """True for a production module of the tree that sits outside the package."""
    return not importer.startswith(f"{view.package}/") and not is_test_file(importer)


def collect_uses(*, view: PackageView) -> dict[str, set[str]]:
    """For each production module outside the package, the members it imports."""
    uses: dict[str, set[str]] = {}
    package_file = f"{view.package}/{PACKAGE_FILE}"
    for member in view.members:
        if member.relative == package_file:
            continue
        for importer in view.graph.find_importers(member.relative):
            if _is_outside_production(importer=importer, view=view):
                uses.setdefault(importer, set()).add(member.relative)
    for importer in view.graph.find_importers(package_file):
        if importer not in view.modules or not _is_outside_production(importer=importer, view=view):
            continue
        found = _collect_facade_uses(view=view, importer=view.modules[importer])
        if found:
            uses.setdefault(importer, set()).update(found)
    return uses


def _measure_internal_coupling(*, view: PackageView) -> tuple[float, float]:
    """The share of member pairs joined by an import, and of members in the largest joined group."""
    members = [member.relative for member in view.members]
    joined = {
        frozenset(pair)
        for pair in combinations(members, 2)
        if pair[1] in view.graph.imports.get(pair[0], ())
        or pair[0] in view.graph.imports.get(pair[1], ())
    }
    group_of = {member: member for member in members}
    for pair in joined:
        first, second = sorted(pair)
        old, new = group_of[second], group_of[first]
        for member, group in group_of.items():
            if group == old:
                group_of[member] = new
    sizes = [list(group_of.values()).count(group) for group in set(group_of.values())]
    pairs = len(members) * (len(members) - 1) // 2
    return len(joined) / pairs, max(sizes) / len(members)


def _measure_importer_similarity(*, view: PackageView, uses: dict[str, set[str]]) -> float | None:
    """The mean pairwise Jaccard similarity of the members' importer sets."""
    importers = {
        member.relative: {importer for importer, used in uses.items() if member.relative in used}
        for member in view.members
    }
    scores = [
        len(importers[first] & importers[second]) / len(importers[first] | importers[second])
        for first, second in combinations(importers, 2)
        if importers[first] | importers[second]
    ]
    return sum(scores) / len(scores) if scores else None


def measure_reuse(*, view: PackageView) -> dict[str, float | int | None]:
    """The Common Reuse signals of one package (``None`` where no importer exists to measure)."""
    uses = collect_uses(view=view)
    count = len(view.members)
    density, connected = _measure_internal_coupling(view=view)
    imported_members = set().union(*uses.values()) if uses else set()
    signals: dict[str, float | int | None] = {
        "internal_density": density,
        "connected_share": connected,
        "importer_count": len(uses),
        "importer_similarity": _measure_importer_similarity(view=view, uses=uses),
        "private_share": 1 - len(imported_members) / count,
        "used_together": None,
        "mean_share_used": None,
    }
    if uses:
        signals["used_together"] = sum(1 for used in uses.values() if len(used) > 1) / len(uses)
        signals["mean_share_used"] = sum(len(used) for used in uses.values()) / (len(uses) * count)
    return signals


def describe_reuse(*, view: PackageView) -> str:
    """One sentence of Common Reuse evidence for a finding's message."""
    uses = collect_uses(view=view)
    count = len(view.members)
    density, _connected = _measure_internal_coupling(view=view)
    coupling = "none of them import one another" if density == 0 else "some import one another"
    if not uses:
        return f"No production module outside the package imports them, and {coupling}."
    if len(uses) == 1:
        ((importer, used),) = uses.items()
        return f"Only '{importer}' imports them ({len(used)} of {count}), and {coupling}."
    together = sum(1 for used in uses.values() if len(used) > 1)
    return (
        f"{len(uses)} modules import them, {together} of those importing two or more, "
        f"and {coupling}."
    )
