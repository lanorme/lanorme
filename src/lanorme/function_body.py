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

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda


def list_body_statements(*, func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
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
    """The names a function binds: parameters, assignment and loop targets,
    walrus and comprehension variables, exception aliases, nested definitions.

    Imports are left out on purpose: ``from shutil import copy`` binds ``copy``
    locally but names one fixed function, so a call to it is kept literal like
    a builtin. A name declared ``global`` or ``nonlocal`` is bound elsewhere.
    """
    bound: set[str] = set()
    declared_elsewhere: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node is not func:
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
