"""Which scope binds a name, answered from a module's shared node index.

A check that asks "is this ``eval`` the builtin?" or "does this ``sql`` name
the module's constant?" needs Python's scoping, not a file-wide name set: a
method named ``exec`` does not shadow the builtin for a module-level call, and
another function's parameter named ``sp`` does not shadow ``import subprocess
as sp``. A :class:`ScopeTree` places a binding in its innermost enclosing
function, lambda or class by source span, so no extra walk of the tree is
needed, and answers visibility the way the interpreter does: the module's own
bindings, then each enclosing function's, and a class body's only for code
directly in that body.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property

from lanorme.sources import Module

# Nodes that open a scope of their own.
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)

# Nodes that bind a plain name and so shadow an import or a builtin.
BINDING_NODES = (
    ast.arg,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.ExceptHandler,
    ast.Assign,
    ast.AnnAssign,
    ast.For,
    ast.AsyncFor,
    ast.comprehension,
    ast.withitem,
    ast.NamedExpr,
)


def _iter_target_names(target: ast.AST | None) -> Iterator[str]:
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.Starred):
        yield from _iter_target_names(target.value)
    elif isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            yield from _iter_target_names(element)


def iter_bound_names(node: ast.AST) -> Iterator[str]:
    """The plain names *node* binds: parameters, def/class names, targets."""
    if isinstance(node, ast.arg):
        yield node.arg
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        yield node.name
    elif isinstance(node, ast.ExceptHandler):
        if node.name:
            yield node.name
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            yield from _iter_target_names(target)
    elif isinstance(node, ast.withitem):
        yield from _iter_target_names(node.optional_vars)
    else:  # AnnAssign, For, AsyncFor, comprehension, NamedExpr
        yield from _iter_target_names(node.target)  # type: ignore[attr-defined]


def _read_anchor(node: ast.AST) -> ast.AST | None:
    """The node whose span places *node*: itself, or its target when it has no position."""
    if isinstance(node, ast.comprehension):
        return node.target
    if isinstance(node, ast.withitem):
        return node.optional_vars
    return node


def _read_span(node: ast.AST) -> tuple[int, int, int, int] | None:
    """``(line, column, end line, end column)`` of *node*, or ``None`` when unplaced."""
    end_line = getattr(node, "end_lineno", None)
    end_column = getattr(node, "end_col_offset", None)
    if end_line is None or end_column is None:
        return None
    return node.lineno, node.col_offset, end_line, end_column  # type: ignore[attr-defined]


def _is_within(*, inner: tuple[int, int, int, int], outer: tuple[int, int, int, int]) -> bool:
    return outer[:2] <= inner[:2] and inner[2:] <= outer[2:]


@dataclass(frozen=True)
class Binding:
    """One binding of *name*: the node that binds it and the scope that owns it.

    *scope* is the innermost function, lambda or class around *node*, or
    ``None`` for the module itself.
    """

    name: str
    node: ast.AST
    scope: ast.AST | None


class ScopeTree:
    """The function, lambda and class scopes of one module, by source span."""

    def __init__(self, module: Module) -> None:
        self._module = module
        self._scopes: list[tuple[tuple[int, int, int, int], ast.AST]] = [
            (span, scope)
            for scope in module.index.collect(*_SCOPE_NODES)
            if (span := _read_span(scope)) is not None
        ]

    def find_enclosing(self, node: ast.AST) -> list[ast.AST]:
        """The scopes around *node*, innermost first; *node* itself is not one."""
        anchor = _read_anchor(node)
        span = _read_span(anchor) if anchor is not None else None
        if span is None:
            return []
        around = [
            (outer, scope)
            for outer, scope in self._scopes
            if scope is not node and _is_within(inner=span, outer=outer)
        ]
        around.sort(key=lambda pair: pair[0][:2], reverse=True)
        return [scope for _span, scope in around]

    def collect_bindings(self, names: frozenset[str]) -> dict[str, list[Binding]]:
        """Every binding of one of *names* in the module, keyed by name."""
        found: dict[str, list[Binding]] = {}
        for node in self._module.index.collect(*BINDING_NODES):
            for name in iter_bound_names(node):
                if name not in names:
                    continue
                enclosing = self.find_enclosing(node)
                owner = enclosing[0] if enclosing else None
                found.setdefault(name, []).append(Binding(name=name, node=node, scope=owner))
        return found

    def find_owner(self, *, bindings: list[Binding], at: ast.AST) -> ast.AST | None:
        """The nearest scope around *at* that binds the name, or ``None`` for the module.

        Python's lookup order: the innermost function or lambda first, then
        each one around it; a class body only for code directly in that body,
        never for its methods. ``None`` means no scope around *at* binds the
        name, so a use there refers to the module's binding, if any.
        """
        owners = {id(binding.scope) for binding in bindings if binding.scope is not None}
        for depth, scope in enumerate(self.find_enclosing(at)):
            if isinstance(scope, ast.ClassDef) and depth > 0:
                continue
            if id(scope) in owners:
                return scope
        return None

    def is_bound_at(self, *, bindings: list[Binding], at: ast.AST) -> bool:
        """True when one of *bindings* is visible from *at* under Python's scoping.

        A module-level binding is visible everywhere; any other when its scope
        is one :meth:`find_owner` would search from *at*.
        """
        if any(binding.scope is None for binding in bindings):
            return True
        return self.find_owner(bindings=bindings, at=at) is not None


class ModuleBindings:
    """The names one module imports and binds anywhere, read on first use.

    A check that needs them only for a rare shape (a comment that could be a
    labelled note) pays for the scan only when that shape turns up.
    """

    def __init__(self, module: Module) -> None:
        self._module = module

    @cached_property
    def imported(self) -> frozenset[str]:
        """Names an ``import`` or ``from ... import`` binds (``import os.path`` binds ``os``)."""
        names: set[str] = set()
        for node in self._module.index.collect(ast.Import, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name.split(".")[0])
        return frozenset(names)

    @cached_property
    def bound(self) -> frozenset[str]:
        """Every name the module binds in any scope, imports included."""
        names = set(self.imported)
        for node in self._module.index.collect(*BINDING_NODES):
            names.update(iter_bound_names(node))
        return frozenset(names)
