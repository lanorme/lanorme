"""Names read off small AST shapes: decorators, dotted attributes, string literals.

Several checks ask the same questions of a node: which name does this
decorator resolve to, what dotted path does this attribute spell, is this a
string literal. The answers live here so each check does not grow its own
slightly different copy.
"""

from __future__ import annotations

import ast


def find_decorator_leaf(
    decorator: ast.expr,
    *,
    calls: bool = True,
    subscripts: bool = False,
) -> str | None:
    """The name a decorator resolves to: ``@app.route("/")`` gives ``route``.

    A name gives its identifier and an attribute its last part. With *calls*
    (the default) any call is looked through, however deep (``@a.b()()``);
    with *subscripts* a subscript is too (``@deco[int]``). Anything else (a
    lambda, a call when *calls* is false) gives ``None``.
    """
    target = decorator
    while (calls and isinstance(target, ast.Call)) or (
        subscripts and isinstance(target, ast.Subscript)
    ):
        target = target.func if isinstance(target, ast.Call) else target.value
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return None


def list_decorator_leaves(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    *,
    calls: bool = True,
    subscripts: bool = False,
) -> list[str | None]:
    """:func:`find_decorator_leaf` of each of *node*'s decorators, in order."""
    return [
        find_decorator_leaf(decorator, calls=calls, subscripts=subscripts)
        for decorator in node.decorator_list
    ]


def build_attr_chain(node: ast.AST | None) -> tuple[str, ...]:
    """The dotted attribute chain at *node*, or ``()`` if it is not one.

    ``hashlib.md5`` gives ``('hashlib', 'md5')``; ``client.x.execute`` gives
    ``('client', 'x', 'execute')``; anything else (a subscript or a call in
    the chain, say) gives ``()``.
    """
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return tuple(reversed(parts))
    return ()


def read_str_constant(node: ast.AST | None) -> str | None:
    """The value of a string literal, or ``None`` for any other node."""
    match node:
        case ast.Constant(value=str() as value):
            return value
    return None
