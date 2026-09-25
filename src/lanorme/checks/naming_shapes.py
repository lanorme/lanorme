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

from lanorme.astnames import list_decorator_leaves
from lanorme.checks.naming_words import (
    CONVERSION_INFIXES,
    CONVERSION_PREFIXES,
    ENTRY_POINTS,
    FRAMEWORK_HOOKS,
    FRAMEWORK_METHODS,
    HOOK_PREFIX_WORDS,
    HOOK_SUFFIX_WORDS,
    PROTOCOL_NAMES,
    split_name,
)
from lanorme.sources import iter_parsed_modules

FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)

# Decorators that leave the name to its author. Any other decorator hands the
# function to a framework (a route, a fixture, a property, a signal, a CLI
# command) that reads the name as a contract.
TRANSPARENT_DECORATORS: frozenset[str] = frozenset(
    {"staticmethod", "classmethod", "abstractmethod", "override", "final"},
)

# Generated migration trees carry names the tool chose.
_SKIP_DIRS = frozenset({"alembic", "migrations"})

# A base whose name ends this way makes the class an exception, whatever it is called.
_EXCEPTION_BASE_ENDINGS: tuple[str, ...] = ("Error", "Exception", "Warning", "Exit", "Interrupt")

_BLOCKS = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.Match,
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

    Generated migration trees are skipped; a file the parser rejects is skipped
    by :func:`iter_parsed_modules` rather than raised.
    """
    for module in iter_parsed_modules(root):
        if any(part in _SKIP_DIRS for part in module.relative.split("/")):
            continue
        yield module.relative, module.tree


def resolve_decorator_leaves(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """The name each decorator resolves to: ``@app.route("/")`` gives ``route``.

    Calls and subscripts are unwrapped, so ``@abc.abstractmethod`` gives
    ``abstractmethod``. A decorator that is not a name or attribute underneath
    (a lambda, say) gives the empty string.
    """
    return {leaf or "" for leaf in list_decorator_leaves(node, subscripts=True)}


def has_opaque_decorator(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if a decorator other than the transparent few claims the name."""
    return bool(resolve_decorator_leaves(node=node) - TRANSPARENT_DECORATORS)


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


def _list_real_statements(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    """The body minus its docstring and bare constants (``...``)."""
    return [
        statement
        for statement in node.body
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
    ]


def is_raiser(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the body ends in ``raise``: the function exists to raise."""
    statements = _list_real_statements(node=node)
    return bool(statements) and isinstance(statements[-1], ast.Raise)


def is_command(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function does something and answers nothing.

    A body that is only a docstring, ``pass`` or ``...`` is a stub (abstract or a
    protocol member), and a body that ends in ``raise`` exists to raise; neither
    is a command in the naming sense.
    """
    statements = _list_real_statements(node=node)
    if not statements or is_raiser(node=node):
        return False
    if len(statements) == 1 and isinstance(statements[0], ast.Pass):
        return False
    return not has_return_value(node=node)


def returns_nested_function(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the body defines a function and returns it, or returns a lambda.

    That is the shape of a decorator or a decorator factory, which Python
    names for what it confers (``deprecated``, ``cached``, ``lru_cache``,
    ``login_required``), and of a closure factory.
    """
    inner: set[str] = set()
    returned: set[str] = set()
    pending: list[ast.AST] = list(node.body)
    while pending:
        current = pending.pop()
        if isinstance(current, FUNCTION_TYPES):
            inner.add(current.name)
            continue
        if isinstance(current, (ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(current, ast.Return):
            if isinstance(current.value, ast.Lambda):
                return True
            if isinstance(current.value, ast.Name):
                returned.add(current.value.id)
        pending.extend(ast.iter_child_nodes(current))
    return bool(inner & returned)


def _resolve_base_leaf(*, base: ast.expr) -> str:
    """The last name of a base expression: ``models.Manager`` gives ``Manager``, ``Generic[T]`` gives ``Generic``."""
    target = base.value if isinstance(base, ast.Subscript) else base
    if isinstance(target, ast.Attribute):
        return target.attr
    return target.id if isinstance(target, ast.Name) else ""


def list_base_leaves(*, node: ast.ClassDef) -> list[str]:
    """The last name of each base: ``class M(models.Manager, Generic[T])`` gives ``["Manager", "Generic"]``."""
    return [_resolve_base_leaf(base=base) for base in node.bases]


def is_exception_class(*, node: ast.ClassDef) -> bool:
    """True if a base is named as an exception (``RuntimeError``, ``Exception``, ``UserWarning``)."""
    return any(leaf.endswith(_EXCEPTION_BASE_ENDINGS) for leaf in list_base_leaves(node=node))


def is_camel_case(*, name: str) -> bool:
    """True for ``mousePressEvent`` and ``setUp``; not for ``set_up``, ``setup`` or ``SetUp``."""
    bare = name.strip("_")
    return "_" not in bare and bare[:1].islower() and bare != bare.lower()


def is_exempt(*, name: str, exempt: frozenset[str]) -> bool:
    """True if *name*, as written or without its leading underscores, is configured exempt."""
    return name in exempt or name.lstrip("_") in exempt


def _is_hook_name(*, name: str) -> bool:
    """``on_click``, ``onMessage``, ``pytest_configure``, ``error_handler``, a bare ``callback``."""
    tokens = split_name(name=name)
    if not tokens:
        return False
    return (len(tokens) > 1 and tokens[0] in HOOK_PREFIX_WORDS) or tokens[-1] in HOOK_SUFFIX_WORDS


def _is_reserved_name(*, name: str) -> bool:
    """A name a convention fixes: dunder, keyword clash, hook, conversion or entry point."""
    if name.startswith("__") and name.endswith("__") or name.endswith("_"):
        return True
    bare = name.lstrip("_")
    if _is_hook_name(name=bare):
        return True
    if bare.startswith(CONVERSION_PREFIXES) or any(infix in bare for infix in CONVERSION_INFIXES):
        return True
    return bare in ENTRY_POINTS or bare in FRAMEWORK_HOOKS


def is_framework_named(*, definition: Definition) -> bool:
    """True if the name was not the author's to choose.

    Dunders, keyword-clash trailing underscores, hook prefixes and suffixes,
    conversion and constructor prefixes, entry points, standard-library
    protocol methods, known framework hooks, a camelCase method on a subclass
    (PEP 8 allows mixedCase only to match a prevailing style, so the name is
    the base API's: ``mousePressEvent``, ``dataReceived``), and anything under
    a decorator that registers the function somewhere.
    """
    if _is_reserved_name(name=definition.name):
        return True
    if definition.is_method and definition.name.lstrip("_") in PROTOCOL_NAMES | FRAMEWORK_METHODS:
        return True
    if definition.may_override and is_camel_case(name=definition.name):
        return True
    return isinstance(definition.node, FUNCTION_TYPES) and has_opaque_decorator(
        node=definition.node,
    )


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
