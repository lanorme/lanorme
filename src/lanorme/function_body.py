"""What a function body binds and what it holds: helpers shared by the clone checks.

DRY-001 and SIMILAR-001 both normalise a function body before comparing it.
Two questions come up in both: which statements make up the body (the leading
docstring is documentation, not a statement) and which names the function
binds itself (a call to a parameter or a local variable targets data, not a
fixed operation, so its name is abstracted like any other variable while a
builtin or an imported name stays literal).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def list_body_statements(*, func: FunctionNode) -> list[ast.stmt]:
    """The function's statements without a leading docstring."""
    body = func.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return list(body)


def collect_local_bindings(*, func: FunctionNode) -> frozenset[str]:
    """The names a function binds in its own scope: parameters, assignment
    and loop targets, walrus and comprehension variables, exception aliases,
    and the names of its nested definitions.

    What a nested function, lambda or class binds is its own and is not
    walked: a lambda parameter named ``len`` does not turn the enclosing
    function's ``len(...)`` into a call through a variable. Imports are left
    out on purpose: ``from shutil import copy`` binds ``copy`` locally but
    names one fixed function, so a call to it is kept literal like a builtin.
    A name declared ``global`` or ``nonlocal`` is bound elsewhere.
    """
    bound: set[str] = set()
    declared_elsewhere: set[str] = set()
    for node in _iter_own_scope(func=func):
        if isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            declared_elsewhere.update(node.names)
    return frozenset(bound - declared_elsewhere)


def _iter_own_scope(*, func: FunctionNode) -> Iterator[ast.AST]:
    """Yield every node of *func*'s own scope: a nested scope yields only itself.

    A nested definition's decorators and default values are evaluated in the
    enclosing scope, but they bind nothing, so stopping at the definition
    loses no binding.
    """
    pending: list[ast.AST] = [*func.args.defaults, *func.args.kw_defaults, func.args]
    pending.extend(func.body)
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, _SCOPES):
            continue
        pending.extend(ast.iter_child_nodes(node))
