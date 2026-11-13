"""Idle boundaries: the members of a package whose module boundary does no work.

A boundary between two modules can do three things a reader or a client
relies on. It can hide an implementation behind an interface
[@parnas1972criteria; @ousterhout2018philosophy, ch. 4]. It can be a namespace
clients write at each reference (``users.router``). It can keep apart two
names that would otherwise collide. A member whose boundary does none of the
three is idle: it is a file of declarations that every client imports by
name, and folding it into a sibling changes nothing but an import path.

- **Idle share.** The share of a package's members that are idle. It has no
  threshold: a member hides something when any function body in it holds a
  statement.
- **Merge evidence.** The idle share plus the co-use in scope, the two things
  that say a split costs its readers and buys them nothing: nothing is
  hidden, and the pieces are read together.
- **Interface shrink.** The share of the package's interface a merge removes.
  Before, a client must know each member's path and each name some other
  module imports; after, one path and the names used outside the package
  [@ousterhout2018philosophy, ch. 9].
- **Hops.** For each client function that references the package, how many
  member files a reader must open beyond the first. Splitting code makes a
  reader move between the pieces, the cost measured for extracted methods in
  [@dacosta2026extract].
- **Signature coupling.** The share of members joined to another by a
  reference in a signature (an annotation or a base class): one data model
  spread over several files.
- **Name echo.** The share of members whose file name is the one public name
  they define (``todo_title.py`` holding ``TodoTitle``): the
  one-declaration-per-file convention, which the module name restates and
  the Lazy Element smell describes [@fowler2018refactoring, ch. 3].
"""

from __future__ import annotations

import ast

from boundary_signals import Boundaries, ClientUse, list_colliding_members
from vocabulary_signals import split_identifier

from lanorme.sources import Module


def list_qualified_members(boundaries: Boundaries) -> set[str]:
    """The members some production module outside the package imports as a module object."""
    return {member for use in boundaries.outside.values() for member in use.namespaces}


def measure_idle_share(boundaries: Boundaries) -> float:
    """The share of members whose boundary hides nothing, names nothing and separates nothing."""
    working = list_colliding_members(boundaries) | list_qualified_members(boundaries)
    idle = [
        surface
        for surface in boundaries.surfaces
        if surface.implementation_size == 0 and surface.relative not in working
    ]
    return len(idle) / len(boundaries.surfaces)


def measure_interface_shrink(boundaries: Boundaries) -> float | None:
    """The share of the package's interface (paths and imported names) a merge removes."""
    used: set[tuple[str, str]] = set()
    used_outside: set[tuple[str, str]] = set()
    for client, use in boundaries.uses.items():
        for member, names in use.names.items():
            found = {(member, name) for name in names}
            used |= found
            if client in boundaries.outside:
                used_outside |= found
    before = len(used) + len(boundaries.surfaces)
    after = len(used_outside) + 1
    return 1 - after / before if before else None


def _count_hops(*, client: Module, use: ClientUse) -> list[int]:
    """For each client function referencing the package, the member files beyond the first."""
    hops: list[int] = []
    for function in client.index.functions:
        referenced = {
            use.local_names[node.id]
            for node in ast.walk(function)
            if isinstance(node, ast.Name) and node.id in use.local_names
        }
        if referenced:
            hops.append(len(referenced) - 1)
    return hops


def measure_hops(boundaries: Boundaries) -> float | None:
    """The mean number of extra member files a client function makes its reader open."""
    hops = [
        count
        for client, use in boundaries.outside.items()
        for count in _count_hops(client=boundaries.view.modules[client], use=use)
    ]
    return sum(hops) / len(hops) if hops else None


def _list_signature_names(module: Module) -> set[str]:
    """Every name a module mentions in an annotation or a base class."""
    expressions: list[ast.expr] = []
    for node in module.index.collect(ast.ClassDef):
        expressions.extend(node.bases)
    for node in module.index.collect(ast.AnnAssign):
        expressions.append(node.annotation)
    for node in module.index.collect(ast.arg):
        if node.annotation is not None:
            expressions.append(node.annotation)
    for node in module.index.functions:
        if node.returns is not None:
            expressions.append(node.returns)
    return {
        found.id
        for expression in expressions
        for found in ast.walk(expression)
        if isinstance(found, ast.Name)
    }


def measure_signature_coupling(boundaries: Boundaries) -> float:
    """The share of members joined to another member by a name in a signature."""
    joined: set[str] = set()
    for surface in boundaries.surfaces:
        own = boundaries.siblings.get(surface.relative)
        if own is None:
            continue
        names = _list_signature_names(boundaries.view.modules[surface.relative])
        targets = {own.local_names[name] for name in names if name in own.local_names}
        targets.discard(surface.relative)
        if targets:
            joined |= {surface.relative, *targets}
    return len(joined) / len(boundaries.surfaces)


def measure_name_echo(boundaries: Boundaries) -> float:
    """The share of members whose file name restates the one public name they define."""
    echoing = 0
    for surface in boundaries.surfaces:
        if len(surface.public) != 1:
            continue
        (name,) = surface.public
        echoing += split_identifier(name) == split_identifier(surface.stem)
    return echoing / len(boundaries.surfaces)


def measure_idle_boundaries(
    *,
    boundaries: Boundaries,
    scope_co_use: float | None,
) -> dict[str, float | None]:
    """The idle share, the merge evidence built on it, and the exploratory merge measures."""
    idle = measure_idle_share(boundaries)
    return {
        "idle_share": idle,
        "merge_evidence": idle + (scope_co_use or 0.0),
        "has_qualified_use": float(bool(list_qualified_members(boundaries))),
        "has_collision": float(bool(list_colliding_members(boundaries))),
        "interface_shrink": measure_interface_shrink(boundaries),
        "hops": measure_hops(boundaries),
        "signature_coupling": measure_signature_coupling(boundaries),
        "name_echo": measure_name_echo(boundaries),
    }
