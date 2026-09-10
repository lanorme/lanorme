"""AST shapes shared by the naming checks: which definitions a rule looks at,
what a body says about whether the function acts or answers, and the names a
rule leaves alone because a framework, a protocol or a convention chose them.

A rule inspects module-level functions and classes and the methods of those
classes. It never descends into a function body: a closure named ``wrapper``
or ``inner`` is local, and the hooks a test registers inline (``before``,
``handle_403``) are named for a framework, not for a reader.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from lanorme.checks.naming_words import (
    CONVERSION_INFIXES,
    CONVERSION_PREFIXES,
    ENTRY_POINTS,
    FRAMEWORK_HOOKS,
    HOOK_PREFIXES,
    HOOK_SUFFIXES,
    PROTOCOL_NAMES,
)
from lanorme.discovery import iter_py_files

FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)

# Decorators that leave the name to its author. Any other decorator hands the
# function to a framework (a route, a fixture, a property, a signal, a CLI
# command) that reads the name as a contract.
TRANSPARENT_DECORATORS: frozenset[str] = frozenset(
    {"staticmethod", "classmethod", "abstractmethod", "override", "final"}
)

# Generated migration trees carry names the tool chose.
_SKIP_DIRS = frozenset({"alembic", "migrations"})

_BLOCKS = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.TryStar, ast.With, ast.AsyncWith, ast.Match,
)


@dataclass(frozen=True)
class Definition:
    """A function or class a naming rule may look at, with the class it belongs to."""

    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    owner: ast.ClassDef | None

    @property
    def name(self) -> str:
        return self.node.name

    @property
    def is_method(self) -> bool:
        return self.owner is not None

    @property
    def may_override(self) -> bool:
        """True for a method on a class with bases, where the name may be inherited."""
        return self.owner is not None and bool(self.owner.bases)


def _block_bodies(*, statement: ast.stmt) -> list[list[ast.stmt]]:
    """The statement lists a compound module-level statement can hide definitions in."""
    if isinstance(statement, ast.Match):
        bodies = [case.body for case in statement.cases]
    elif isinstance(statement, (ast.Try, ast.TryStar)):
        handlers = [handler.body for handler in statement.handlers]
        bodies = [statement.body, statement.orelse, statement.finalbody, *handlers]
    elif isinstance(statement, (ast.With, ast.AsyncWith)):
        bodies = [statement.body]
    else:
        bodies = [statement.body, statement.orelse]
    return [body for body in bodies if body]


def iter_definitions(*, tree: ast.Module) -> Iterator[Definition]:
    """Module-level functions and classes, and the methods of those classes.

    Definitions inside a function body are never yielded. Module-level ``if``,
    ``match``, ``for``, ``while``, ``try`` and ``with`` blocks are looked
    through, so a platform-guarded definition still counts.
    """
    pending: list[tuple[list[ast.stmt], ast.ClassDef | None]] = [(tree.body, None)]
    while pending:
        body, owner = pending.pop()
        for statement in body:
            if isinstance(statement, FUNCTION_TYPES):
                yield Definition(node=statement, owner=owner)
            elif isinstance(statement, ast.ClassDef):
                yield Definition(node=statement, owner=owner)
                pending.append((statement.body, statement))
            elif isinstance(statement, _BLOCKS):
                pending.extend((block, owner) for block in _block_bodies(statement=statement))


def iter_modules(*, root: Path) -> Iterator[tuple[str, ast.Module]]:
    """Every parseable module under *root* with its root-relative posix path.

    Parsing from bytes honours a BOM and a coding cookie. A file the parser
    rejects, including one that overflows it, is skipped rather than raised.
    """
    for path in iter_py_files(root):
        relative = path.relative_to(root)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except (OSError, SyntaxError, ValueError, RecursionError, MemoryError):
            continue
        yield relative.as_posix(), tree


def decorator_leaves(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """The name each decorator resolves to: ``@app.route("/")`` gives ``route``.

    Calls and subscripts are unwrapped, so ``@abc.abstractmethod`` gives
    ``abstractmethod``. A decorator that is not a name or attribute underneath
    (a lambda, say) gives the empty string.
    """
    leaves: set[str] = set()
    for decorator in node.decorator_list:
        target: ast.expr = decorator
        while isinstance(target, (ast.Call, ast.Subscript)):
            target = target.func if isinstance(target, ast.Call) else target.value
        if isinstance(target, ast.Attribute):
            leaves.add(target.attr)
        elif isinstance(target, ast.Name):
            leaves.add(target.id)
        else:
            leaves.add("")
    return leaves


def has_opaque_decorator(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if a decorator other than the transparent few claims the name."""
    return bool(decorator_leaves(node=node) - TRANSPARENT_DECORATORS)


def has_return_value(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the body answers with a value: ``return x`` (``x`` may be ``None``) or a ``yield``."""
    pending: list[ast.AST] = list(node.body)
    while pending:
        current = pending.pop()
        if isinstance(current, (*FUNCTION_TYPES, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(current, ast.Return) and current.value is not None:
            return True
        if isinstance(current, (ast.Yield, ast.YieldFrom)):
            return True
        pending.extend(ast.iter_child_nodes(current))
    return False


def _real_statements(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    """The body minus its docstring and bare constants (``...``)."""
    return [
        statement for statement in node.body
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
    ]


def is_raiser(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the body ends in ``raise``: the function exists to raise."""
    statements = _real_statements(node=node)
    return bool(statements) and isinstance(statements[-1], ast.Raise)


def is_command(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function does something and answers nothing.

    A body that is only a docstring, ``pass`` or ``...`` is a stub (abstract or a
    protocol member), and a body that ends in ``raise`` exists to raise; neither
    is a command in the naming sense.
    """
    statements = _real_statements(node=node)
    if not statements or is_raiser(node=node):
        return False
    if len(statements) == 1 and isinstance(statements[0], ast.Pass):
        return False
    return not has_return_value(node=node)


def is_exempt(*, name: str, exempt: frozenset[str]) -> bool:
    """True if *name*, as written or without its leading underscores, is configured exempt."""
    return name in exempt or name.lstrip("_") in exempt


def _is_reserved_name(*, name: str) -> bool:
    """A name a convention fixes: dunder, keyword clash, hook, conversion or entry point."""
    if name.startswith("__") and name.endswith("__") or name.endswith("_"):
        return True
    bare = name.lstrip("_")
    if bare.startswith(HOOK_PREFIXES) or bare.endswith(HOOK_SUFFIXES):
        return True
    if bare.startswith(CONVERSION_PREFIXES) or any(infix in bare for infix in CONVERSION_INFIXES):
        return True
    return bare in ENTRY_POINTS or bare in FRAMEWORK_HOOKS


def is_framework_named(*, definition: Definition) -> bool:
    """True if the name was not the author's to choose.

    Dunders, keyword-clash trailing underscores, hook prefixes and suffixes,
    conversion and constructor prefixes, entry points, standard-library
    protocol methods, known framework hooks, and anything under a decorator
    that registers the function somewhere.
    """
    if _is_reserved_name(name=definition.name):
        return True
    if definition.is_method and definition.name.lstrip("_") in PROTOCOL_NAMES:
        return True
    return isinstance(definition.node, FUNCTION_TYPES) and has_opaque_decorator(node=definition.node)


def name_setting(*, settings: dict[str, bool | list[str]], key: str) -> list[str] | None:
    """The list of names under *key*, ``None`` if absent.

    ``TypeError`` if it is not a list of strings, ``ValueError`` if an entry is
    not a single identifier, so ``verbs = ["frob nicate"]`` fails loudly
    instead of never matching.
    """
    if key not in settings:
        return None
    value = settings[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"'{key}' must be a list of strings")
    if not all(item.isidentifier() for item in value):
        raise ValueError(f"'{key}' entries must be single names")
    return value
