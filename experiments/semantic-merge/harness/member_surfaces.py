"""What each member of a package declares, and how much implementation sits behind it.

A module boundary earns its place by what it hides [@parnas1972criteria]: a
deep module puts much functionality behind a small interface, a shallow one
has an interface about as large as its body [@ousterhout2018philosophy, ch.
4]. This module measures that for the members of a package, on the syntax
tree alone:

- **Interface.** What a client must know to use the module: each top-level
  function, class and assigned name, each parameter, and each method and
  field of a class.
- **Implementation.** The statements inside function and method bodies,
  docstrings and placeholders (``pass``, ``...``, ``raise
  NotImplementedError``) left out.
- **Depth.** Implementation over implementation plus interface. A module of
  declarations only (exceptions, constants, Protocols, data classes) has
  depth 0: there is nothing behind its interface for the boundary to hide.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lanorme.sources import Module

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_PLACEHOLDER_ERROR = "NotImplementedError"


@dataclass(frozen=True)
class MemberSurface:
    """One member's declared names and the sizes of its interface and implementation."""

    relative: str
    stem: str
    public: frozenset[str]
    defined: frozenset[str]
    interface_size: int
    implementation_size: int

    @property
    def depth(self) -> float:
        """The share of the member that is implementation behind its interface."""
        total = self.interface_size + self.implementation_size
        return self.implementation_size / total if total else 0.0


def _is_placeholder(statement: ast.stmt) -> bool:
    """True for a statement that stands in for a body: a docstring, ``pass``, ``...``, a stub raise."""
    match statement:
        case ast.Pass() | ast.Expr(value=ast.Constant()):
            return True
        case ast.Raise(exc=ast.Name(id=name)) | ast.Raise(exc=ast.Call(func=ast.Name(id=name))):
            return name == _PLACEHOLDER_ERROR
    return False


def _count_implementation(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """The statements of a function's body, nested ones included, placeholders left out."""
    return sum(
        1
        for child in function.body
        for node in ast.walk(child)
        if isinstance(node, ast.stmt) and not _is_placeholder(node)
    )


def _count_parameters(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    arguments = function.args
    listed = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    return len(listed) + (arguments.vararg is not None) + (arguments.kwarg is not None)


def _measure_statement(statement: ast.stmt) -> tuple[int, int]:
    """The (interface, implementation) sizes of one top-level or class-level statement."""
    match statement:
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            return 1 + _count_parameters(statement), _count_implementation(statement)
        case ast.ClassDef(body=body):
            sizes = [_measure_statement(child) for child in body]
            interface = 1 + sum(found[0] for found in sizes)
            return interface, sum(found[1] for found in sizes)
        case ast.Assign() | ast.AnnAssign():
            return 1, 0
    return 0, 0


def _list_defined_names(statement: ast.stmt) -> list[str]:
    """The names one top-level statement binds, imports left out."""
    match statement:
        case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.ClassDef(name=name):
            return [name]
        case ast.Assign(targets=targets):
            return [target.id for target in targets if isinstance(target, ast.Name)]
        case ast.AnnAssign(target=ast.Name(id=name)):
            return [name]
    return []


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def read_surface(module: Module) -> MemberSurface:
    """The declared names of *module* and the sizes of its interface and implementation."""
    sizes = [_measure_statement(statement) for statement in module.tree.body]
    names = [
        name
        for statement in module.tree.body
        for name in _list_defined_names(statement)
        if not _is_dunder(name)
    ]
    return MemberSurface(
        relative=module.relative,
        stem=module.relative.rsplit("/", 1)[-1].removesuffix(".py"),
        public=frozenset(name for name in names if not name.startswith("_")),
        defined=frozenset(names),
        interface_size=sum(found[0] for found in sizes),
        implementation_size=sum(found[1] for found in sizes),
    )


def list_definitions(module: Module) -> frozenset[str]:
    """The top-level names *module* binds with ``def`` or ``class``."""
    return frozenset(
        statement.name
        for statement in module.tree.body
        if isinstance(statement, (*_FUNCTIONS, ast.ClassDef))
    )


def measure_depth(surfaces: list[MemberSurface]) -> dict[str, float | None]:
    """The pooled depth of a package's members and the share of them that hide nothing."""
    if not surfaces:
        return {"depth": None, "declaration_only_share": None}
    interface = sum(surface.interface_size for surface in surfaces)
    implementation = sum(surface.implementation_size for surface in surfaces)
    total = interface + implementation
    return {
        "depth": implementation / total if total else 0.0,
        "declaration_only_share": sum(1 for surface in surfaces if surface.implementation_size == 0)
        / len(surfaces),
    }
