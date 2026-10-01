"""The import graph of a scanned tree: which in-tree module imports which.

A cross-file rule that asks "who imports this module?" or "would merging these
modules close an import cycle?" needs every import resolved to a file. This
module does that once per call from the shared per-file views
(``module.imports``), so a rule never writes its own resolver.

**Names.** A package is a directory holding an ``__init__.py`` (the scan
root's own ``__init__.py`` does not make the root one). A file's dotted name
climbs through the packages above it; what is left above the top-level package
is its import root (``src`` for ``src/pkg/x.py``, which is ``pkg.x``; ``""``
for ``pkg/x.py``). Two files that claim one name in one root, as ``pkg/a.py``
and ``pkg/a/__init__.py`` do, are ambiguous: both are left out of the graph
rather than guessed between.

**Resolution.** Every import in a file counts, those inside functions and
under ``if TYPE_CHECKING:`` included. A relative import resolves inside the
importer's own root; one that climbs above it stays unresolved. An absolute
import resolves in the importer's root first and otherwise in the one other
root that holds the name, which covers a ``tests/`` tree importing a ``src/``
layout; a name two other roots hold stays unresolved. ``from pkg import x``
records ``pkg.x`` when it is a module, and the longest known prefix of the
imported name (``pkg``) for ``from`` and plain imports alike. Namespace
packages and ``importlib`` strings resolve to nothing, so the graph can only
miss an edge, never invent one.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from lanorme.source_views import ImportedModule
from lanorme.sources import Module

_PACKAGE_FILE = "__init__.py"
# Module-level hooks a PEP 562 shim defines in place of real code.
_MODULE_HOOKS = frozenset({"__getattr__", "__dir__"})


@dataclass(frozen=True)
class ModuleNode:
    """One unambiguously named module of the tree."""

    relative: str
    root: str
    dotted: str
    is_package: bool


@dataclass(frozen=True)
class ModuleGraph:
    """The in-tree modules, the packages, and the resolved import edges in both directions."""

    nodes: Mapping[str, ModuleNode]
    packages: frozenset[str]
    importers: Mapping[str, frozenset[str]]
    imports: Mapping[str, frozenset[str]]

    def find_importers(self, relative: str) -> frozenset[str]:
        """The modules that import the module at *relative*."""
        return self.importers.get(relative, frozenset())

    def can_reach(self, *, start: str, goal: str) -> bool:
        """True when a chain of imports leads from *start* to *goal*."""
        stack, seen = [start], {start}
        while stack:
            current = stack.pop()
            for target in self.imports.get(current, ()):
                if target == goal:
                    return True
                if target not in seen:
                    seen.add(target)
                    stack.append(target)
        return False

    def find_cycle_through(self, *, group: frozenset[str]) -> str | None:
        """The outside module a merge of *group* would put on a new import cycle, or ``None``.

        For each member, follow its imports that leave *group* without
        re-entering it. Reaching a different member that cannot already reach
        the first one means the merged module would import itself through the
        outside path: a cycle that does not exist today. The first outside
        module on that path is returned. A path back to the same member, or
        between two members already on one cycle, is not new.
        """
        for member in sorted(group):
            found = self._find_outside_path(member=member, group=group)
            if found is not None:
                return found
        return None

    def _find_outside_path(self, *, member: str, group: frozenset[str]) -> str | None:
        first = [target for target in self.imports.get(member, ()) if target not in group]
        entry_of = {target: target for target in first}
        stack = list(first)
        while stack:
            current = stack.pop()
            for target in self.imports.get(current, ()):
                if target in group:
                    if target != member and not self.can_reach(start=target, goal=member):
                        return entry_of[current]
                    continue
                if target not in entry_of:
                    entry_of[target] = entry_of[current]
                    stack.append(target)
        return None


def _split_import_name(*, relative: str, packages: frozenset[str]) -> tuple[str, str] | None:
    """The (import root, dotted name) of the file at *relative*, or ``None`` for the root's own."""
    parts = relative.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return None
    start = len(parts) - 1
    while start > 0 and "/".join(parts[:start]) in packages:
        start -= 1
    return "/".join(parts[:start]), ".".join(parts[start:])


@dataclass(frozen=True)
class _NameTable:
    """Where each dotted name lives: per root the unambiguous names, and the roots of each name."""

    by_root: Mapping[str, Mapping[str, str]]
    roots_of: Mapping[str, frozenset[str]]


def _list_candidate_names(*, target: str, imported: ImportedModule) -> tuple[list[str], list[str]]:
    """The submodules a ``from`` import may name, and the prefixes of *target*, longest first."""
    submodules: list[str] = []
    if imported.is_from:
        submodules = [
            f"{target}.{alias.name}" if target else alias.name
            for alias in imported.aliases
            if alias.name != "*"
        ]
    parts = target.split(".") if target else []
    prefixes = [".".join(parts[:end]) for end in range(len(parts), 0, -1)]
    return submodules, prefixes


def _look_up_in_root(
    *,
    names: Mapping[str, str],
    submodules: list[str],
    prefixes: list[str],
) -> set[str]:
    found = {names[name] for name in submodules if name in names}
    for prefix in prefixes:
        if prefix in names:
            found.add(names[prefix])
            break
    return found


def _resolve_relative_target(*, node: ModuleNode, imported: ImportedModule) -> str | None:
    """The absolute dotted name a relative import names, or ``None`` when it climbs too far."""
    base = node.dotted.split(".")
    if not node.is_package:
        base = base[:-1]
    climb = imported.level - 1
    if climb > len(base):
        return None
    base = base[: len(base) - climb]
    return ".".join([*base, imported.module] if imported.module else base)


def _resolve_import(*, node: ModuleNode, imported: ImportedModule, table: _NameTable) -> set[str]:
    """The relative paths of the in-tree modules one import names."""
    own = table.by_root.get(node.root, {})
    if imported.level:
        target = _resolve_relative_target(node=node, imported=imported)
        if target is None:
            return set()
        submodules, prefixes = _list_candidate_names(target=target, imported=imported)
        return _look_up_in_root(names=own, submodules=submodules, prefixes=prefixes)
    submodules, prefixes = _list_candidate_names(target=imported.module, imported=imported)
    found = _look_up_in_root(names=own, submodules=submodules, prefixes=prefixes)
    if found:
        return found
    other_roots: set[str] = set()
    for name in (*submodules, *prefixes):
        other_roots |= table.roots_of.get(name, frozenset()) - {node.root}
    if len(other_roots) != 1:
        return set()
    names = table.by_root[other_roots.pop()]
    return _look_up_in_root(names=names, submodules=submodules, prefixes=prefixes)


def _build_name_table(
    *,
    modules: list[Module],
    packages: frozenset[str],
) -> tuple[dict[str, ModuleNode], _NameTable]:
    """Name every module, dropping the ambiguous ones, and index the names by root."""
    claimed: dict[tuple[str, str], list[str]] = defaultdict(list)
    for module in modules:
        name = _split_import_name(relative=module.relative, packages=packages)
        if name is not None:
            claimed[name].append(module.relative)
    nodes: dict[str, ModuleNode] = {}
    by_root: dict[str, dict[str, str]] = defaultdict(dict)
    roots_of: dict[str, set[str]] = defaultdict(set)
    for (root, dotted), relatives in claimed.items():
        if len(relatives) != 1:
            continue
        relative = relatives[0]
        nodes[relative] = ModuleNode(
            relative=relative,
            root=root,
            dotted=dotted,
            is_package=relative.rsplit("/", 1)[-1] == _PACKAGE_FILE,
        )
        by_root[root][dotted] = relative
        roots_of[dotted].add(root)
    table = _NameTable(
        by_root=by_root,
        roots_of={name: frozenset(roots) for name, roots in roots_of.items()},
    )
    return nodes, table


def build_module_graph(*, modules: Iterable[Module]) -> ModuleGraph:
    """Resolve every import of *modules* to the in-tree modules it names."""
    listed = list(modules)
    packages = frozenset(
        module.relative.rsplit("/", 1)[0]
        for module in listed
        if module.relative.endswith(f"/{_PACKAGE_FILE}")
    )
    nodes, table = _build_name_table(modules=listed, packages=packages)
    importers: dict[str, set[str]] = defaultdict(set)
    imports: dict[str, set[str]] = defaultdict(set)
    for module in listed:
        node = nodes.get(module.relative)
        if node is None:
            continue
        for imported in module.imports:
            for target in _resolve_import(node=node, imported=imported, table=table):
                if target != node.relative:
                    importers[target].add(node.relative)
                    imports[node.relative].add(target)
    return ModuleGraph(
        nodes=nodes,
        packages=packages,
        importers={target: frozenset(found) for target, found in importers.items()},
        imports={source: frozenset(found) for source, found in imports.items()},
    )


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _list_branch_statements(statement: ast.If | ast.Try) -> list[ast.stmt]:
    """Every statement directly in an ``if`` or ``try``: each branch, handler, ``else`` and ``finally``."""
    if isinstance(statement, ast.If):
        return [*statement.body, *statement.orelse]
    found = [*statement.body, *statement.orelse, *statement.finalbody]
    found.extend(child for handler in statement.handlers for child in handler.body)
    return found


def _is_reexport_statement(statement: ast.stmt) -> bool:
    """True for a statement a re-export or compatibility shim is made of."""
    match statement:
        case (
            ast.Import()
            | ast.ImportFrom()
            | ast.Pass()
            | ast.Delete()
            | ast.Expr(
                value=ast.Constant(),
            )
        ):
            return True
        case ast.FunctionDef(name=name):
            return name in _MODULE_HOOKS
        case ast.Assign(targets=targets):
            return all(isinstance(target, ast.Name) and _is_dunder(target.id) for target in targets)
        case ast.AnnAssign(target=ast.Name(id=name)) | ast.AugAssign(target=ast.Name(id=name)):
            return _is_dunder(name)
        case ast.If() | ast.Try():
            return all(
                _is_reexport_statement(child) for child in _list_branch_statements(statement)
            )
    return False


def is_reexport_only(*, tree: ast.Module) -> bool:
    """True when a module only re-exports: imports, dunder assignments and PEP 562 hooks.

    A docstring or another constant expression, ``pass`` and ``del`` are
    allowed too, as is an ``if`` or ``try`` whose every branch holds only these.
    An empty module is re-export only.
    """
    return all(_is_reexport_statement(statement) for statement in tree.body)
