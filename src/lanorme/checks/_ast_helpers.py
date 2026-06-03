"""Shared AST helper utilities for built-in checks."""

from __future__ import annotations

import ast


def attr_chain(node: ast.AST) -> tuple[str, ...]:
    """Return the dotted attribute chain at *node*, or () if it is not one.

    ``hashlib.md5`` -> ('hashlib', 'md5');
    ``client.x.execute`` -> ('client', 'x', 'execute');
    anything else (subscripts, calls in the chain, etc.) -> ().
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
