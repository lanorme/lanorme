"""Boundary work: what the module boundaries inside a small package do for its clients.

The similarity signals of this spike ask whether the members of a package
resemble each other, and resemblance is as true of a deliberate convention as
of an over-split. This module asks a different question of each boundary: does
it do any work? A module is a namespace and an information-hiding unit
[@parnas1972criteria]; merging a package removes its inner boundaries, as
Inline Class removes a class that no longer pays for itself
[@fowler2018refactoring, ch. 7]. The merge is simulated on the names the
members define and the names their clients import, and five things are
measured:

- **Collisions.** The share of members defining a name another member also
  defines. Colliding names mean the namespace disambiguates (each router
  module binds ``router``). A refactoring preserves behaviour only when its
  preconditions hold [@opdyke1992refactoring], and a merge that needs
  renames is one whose precondition fails.
- **Qualified use.** Of the client-to-member uses, the share that import the
  member as a module object (``from pkg import users``) and so write its
  name at every reference.
- **Facade use.** The share that never name the member at all and import
  from the package, whose ``__init__.py`` re-exports: to those clients the
  package already is one module.
- **Sibling-only names.** The share of public names that only sibling members
  import. They are interface today and would be private after a merge, the
  case where combining modules simplifies the interface
  [@ousterhout2018philosophy, ch. 9].
- **Co-use in scope.** Of the client functions that reference any member's
  names, the share referencing two or more members: the Common Reuse
  Principle [@martin2002agile, ch. 20] at the granularity of a function, not
  a file.

A sixth, the merge saving, is the share of the members' import aliases a
merge deletes: imports of each other and imports they repeat.

Uses are read from import statements and from attribute access on an
imported module object; a name reached any other way (``getattr``, a
framework registry) is not seen.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from member_surfaces import MemberSurface, list_definitions
from reuse_signals import PackageView, collect_reexports

from lanorme.module_graph import PACKAGE_FILE
from lanorme.paths import is_test_file
from lanorme.source_views import ImportedModule
from lanorme.sources import Module

_PACKAGE_STEM = "__init__"


@dataclass
class ClientUse:
    """What one client module takes from the members of a package."""

    names: dict[str, set[str]] = field(default_factory=dict)
    local_names: dict[str, str] = field(default_factory=dict)
    namespaces: set[str] = field(default_factory=set)
    facade: set[str] = field(default_factory=set)
    matched_aliases: int = 0

    @property
    def members(self) -> set[str]:
        """Every member the client uses, by name or as a namespace."""
        return set(self.names) | self.namespaces

    def record(self, *, member: str, name: str, local: str) -> None:
        """Note that the client imports *name* from *member* under *local*."""
        self.names.setdefault(member, set()).add(name)
        self.local_names[local] = member
        self.matched_aliases += 1


@dataclass(frozen=True)
class Boundaries:
    """A package's members, what each defines, and how every client of the tree uses them."""

    view: PackageView
    surfaces: tuple[MemberSurface, ...]
    uses: dict[str, ClientUse]

    @property
    def outside(self) -> dict[str, ClientUse]:
        """The uses by production modules outside the package."""
        inside = f"{self.view.package}/"
        return {
            client: use
            for client, use in self.uses.items()
            if not client.startswith(inside) and not is_test_file(client)
        }

    @property
    def siblings(self) -> dict[str, ClientUse]:
        """The uses by the package's own modules."""
        inside = f"{self.view.package}/"
        return {client: use for client, use in self.uses.items() if client.startswith(inside)}


@dataclass(frozen=True)
class PackageNames:
    """How a package can be named in an import: its members' stems, what it re-exports, and
    the names its own ``__init__.py`` defines when that file is a member."""

    package: str
    stems: dict[str, str]
    exported: dict[str, str]
    own: frozenset[str] = frozenset()

    def is_named_by(self, imported: ImportedModule) -> bool:
        """True when a ``from`` import names the package itself, absolutely or as ``from .``."""
        if imported.level and not imported.module:
            return True
        return imported.module.rsplit(".", 1)[-1] == self.package.rsplit("/", 1)[-1]


@dataclass
class UseReader:
    """Reads one client's imports into a :class:`ClientUse`."""

    names: PackageNames
    reachable: frozenset[str]
    reaches_package: bool
    use: ClientUse = field(default_factory=ClientUse)

    def _find_member(self, stem: str) -> str | None:
        """The member called *stem*, when the client's imports reach it."""
        member = self.names.stems.get(stem)
        return member if member in self.reachable else None

    def _read_package_alias(self, alias: ast.alias) -> None:
        """Record one name imported from the package: a member as a namespace, or a re-export."""
        local = alias.asname or alias.name
        member = self._find_member(alias.name)
        if member is not None:
            self.use.namespaces.add(member)
            self.use.local_names[local] = member
            self.use.matched_aliases += 1
        elif alias.name in self.names.exported:
            source = self.names.exported[alias.name]
            self.use.record(member=source, name=alias.name, local=local)
            self.use.facade.add(source)
        elif alias.name in self.names.own:
            package_file = f"{self.names.package}/{PACKAGE_FILE}"
            self.use.record(member=package_file, name=alias.name, local=local)

    def read(self, imported: ImportedModule) -> None:
        """Record what one ``from`` import takes from the package's members."""
        member = self._find_member(imported.module.rsplit(".", 1)[-1])
        if member is not None:
            for alias in imported.aliases:
                self.use.record(member=member, name=alias.name, local=alias.asname or alias.name)
        elif self.reaches_package and self.names.is_named_by(imported):
            for alias in imported.aliases:
                self._read_package_alias(alias)


def _resolve_attributes(*, client: Module, use: ClientUse) -> None:
    """Add the names a client reads off a member it imported as a module object."""
    for node in client.index.collect(ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in use.local_names:
            member = use.local_names[node.value.id]
            if member in use.namespaces:
                use.names.setdefault(member, set()).add(node.attr)


def resolve_use(*, view: PackageView, names: PackageNames, client: Module) -> ClientUse:
    """What *client* imports from the members of the package, read from its import statements."""
    reachable = view.graph.imports.get(client.relative, frozenset())
    inside = client.relative.startswith(f"{view.package}/")
    reader = UseReader(
        names=names,
        reachable=reachable,
        reaches_package=inside or f"{view.package}/{PACKAGE_FILE}" in reachable,
    )
    for imported in client.imports:
        if imported.is_from:
            reader.read(imported)
    _resolve_attributes(client=client, use=reader.use)
    return reader.use


def read_boundaries(*, view: PackageView, surfaces: list[MemberSurface]) -> Boundaries:
    """Resolve how every module of the tree uses the members of *view*'s package."""
    names = PackageNames(
        package=view.package,
        stems={
            surface.stem: surface.relative for surface in surfaces if surface.stem != _PACKAGE_STEM
        },
        exported=collect_reexports(view=view),
        own=frozenset(
            name
            for surface in surfaces
            if surface.stem == _PACKAGE_STEM
            for name in surface.defined
        ),
    )
    package_file = f"{view.package}/{PACKAGE_FILE}"
    uses: dict[str, ClientUse] = {}
    for relative, client in view.modules.items():
        if relative == package_file:
            continue
        found = resolve_use(view=view, names=names, client=client)
        if found.members:
            uses[relative] = found
    return Boundaries(view=view, surfaces=tuple(surfaces), uses=uses)


def list_colliding_members(boundaries: Boundaries) -> set[str]:
    """The members defining a name another member defines too, when the name is in use.

    A name bound by ``def`` or ``class``, or one some module imports, is in
    use. A repeated plain assignment nothing imports (``logger =
    getLogger(...)``) merges without a rename and does not count.
    """
    imported: dict[str, set[str]] = {}
    for use in boundaries.uses.values():
        for member, names in use.names.items():
            imported.setdefault(member, set()).update(names)
    modules = boundaries.view.modules
    owners: dict[str, list[MemberSurface]] = {}
    for surface in boundaries.surfaces:
        for name in surface.defined:
            owners.setdefault(name, []).append(surface)
    colliding: set[str] = set()
    for name, found in owners.items():
        in_use = any(
            name in list_definitions(modules[surface.relative])
            or name in imported.get(surface.relative, set())
            for surface in found
        )
        if len(found) > 1 and in_use:
            colliding.update(surface.relative for surface in found)
    return colliding


def measure_collisions(boundaries: Boundaries) -> float:
    """The share of members defining a name that another member defines too."""
    return len(list_colliding_members(boundaries)) / len(boundaries.surfaces)


def measure_addressing(boundaries: Boundaries) -> dict[str, float | None]:
    """How outside clients address the members: as namespaces, through the facade, or by path."""
    pairs = [(use, member) for use in boundaries.outside.values() for member in use.members]
    if not pairs:
        return {"qualified_use_share": None, "facade_use_share": None}
    qualified = sum(1 for use, member in pairs if member in use.namespaces)
    facade = sum(1 for use, member in pairs if member in use.facade)
    return {
        "qualified_use_share": qualified / len(pairs),
        "facade_use_share": facade / len(pairs),
    }


def measure_sibling_only_names(boundaries: Boundaries) -> float | None:
    """The share of the members' public names that only sibling members import."""
    public = {
        (surface.relative, name) for surface in boundaries.surfaces for name in surface.public
    }
    if not public:
        return None
    by_outside = {
        (member, name)
        for use in boundaries.outside.values()
        for member, names in use.names.items()
        for name in names
    }
    by_siblings = {
        (member, name)
        for client, use in boundaries.siblings.items()
        for member, names in use.names.items()
        if member != client
        for name in names
    }
    return len((public & by_siblings) - by_outside) / len(public)


def _count_scopes(*, client: Module, use: ClientUse) -> tuple[int, int]:
    """The client functions referencing any member, and those referencing two or more."""
    any_member = several = 0
    for function in client.index.functions:
        referenced = {
            use.local_names[node.id]
            for node in ast.walk(function)
            if isinstance(node, ast.Name) and node.id in use.local_names
        }
        any_member += bool(referenced)
        several += len(referenced) > 1
    return any_member, several


def measure_scope_co_use(boundaries: Boundaries) -> float | None:
    """Of the outside functions referencing a member's names, the share referencing several."""
    counts = [
        _count_scopes(client=boundaries.view.modules[client], use=use)
        for client, use in boundaries.outside.items()
    ]
    referencing = sum(found[0] for found in counts)
    return sum(found[1] for found in counts) / referencing if referencing else None


def measure_merge_saving(boundaries: Boundaries) -> float | None:
    """The share of the members' import aliases a merge deletes: sibling imports and repeats."""
    total = 0
    kept: set[tuple[int, str, str]] = set()
    for surface in boundaries.surfaces:
        module = boundaries.view.modules[surface.relative]
        own = boundaries.siblings.get(surface.relative, ClientUse())
        aliases = [
            (imported.level, imported.module, alias.name)
            for imported in module.imports
            for alias in imported.aliases
        ]
        total += len(aliases)
        external = [found for found in aliases if found[2] not in own.local_names]
        kept.update(external)
    return 1 - len(kept) / total if total else None


def measure_boundaries(boundaries: Boundaries) -> dict[str, float | None]:
    """Every boundary-work signal of one package."""
    signals: dict[str, float | None] = {"collision_share": measure_collisions(boundaries)}
    signals.update(measure_addressing(boundaries))
    signals["sibling_only_names"] = measure_sibling_only_names(boundaries)
    signals["scope_co_use"] = measure_scope_co_use(boundaries)
    signals["merge_saving"] = measure_merge_saving(boundaries)
    return signals
