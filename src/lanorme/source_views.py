"""Lazily computed views of one parsed file, shared by every check in a run.

Several checks walk the same file for the same thing: its lines, its ``#``
comments, its docstrings, its imports. :class:`SourceViews` computes each on
first use and keeps it with the file's cached parse, so a view is built once
per file per run however many checks read it. ``lanorme.sources.Module``
exposes them as properties.

Views are shared, like the tree they come from: a check must never mutate one.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from lanorme.comment_code import Comment, CommentScan, collect_comments

if TYPE_CHECKING:
    from lanorme.sources import NodeIndex

# The nodes that can carry a docstring, in the order ``ast.get_docstring`` accepts.
DocstringOwner = ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Docstring:
    """The docstring of a module, class or function, and the string node that holds it.

    *text* is the string as written (``ast.get_docstring(owner, clean=False)``)
    and *line* the line it starts on.
    """

    owner: DocstringOwner
    node: ast.Constant

    @property
    def text(self) -> str:
        """The docstring as written, indentation and all."""
        return self.node.value

    @property
    def line(self) -> int:
        """The 1-based line the string literal starts on."""
        return self.node.lineno

    def clean(self) -> str:
        """The docstring as ``ast.get_docstring(owner)`` returns it (``inspect.cleandoc``)."""
        return inspect.cleandoc(self.text)


@dataclass(frozen=True)
class ImportedModule:
    """One module an import statement names, with the names it takes from it.

    ``import a.b, c`` gives two entries (``a.b`` and ``c``), each with its own
    alias; ``from a.b import c, d`` gives one entry for ``a.b`` holding both
    aliases. *module* is ``""`` for a bare relative import (``from . import x``)
    and *level* counts the leading dots.
    """

    node: ast.Import | ast.ImportFrom
    module: str
    aliases: tuple[ast.alias, ...]
    level: int = 0

    @property
    def is_from(self) -> bool:
        """True for a ``from ... import ...`` statement."""
        return isinstance(self.node, ast.ImportFrom)


def _find_docstring_node(owner: DocstringOwner) -> ast.Constant | None:
    """The leading string constant of *owner*'s body, the test ``ast.get_docstring`` applies."""
    match owner.body:
        case [ast.Expr(value=ast.Constant(value=str()) as node), *_]:
            return node
    return None


def _list_imported_modules(node: ast.Import | ast.ImportFrom) -> list[ImportedModule]:
    if isinstance(node, ast.Import):
        return [
            ImportedModule(node=node, module=alias.name, aliases=(alias,)) for alias in node.names
        ]
    return [
        ImportedModule(
            node=node,
            module=node.module or "",
            aliases=tuple(node.names),
            level=node.level,
        ),
    ]


class SourceViews:
    """The views of one parsed file, each computed on first use and then kept."""

    def __init__(self, *, source: str, index: NodeIndex) -> None:
        self._source = source
        self._index = index

    @cached_property
    def lines(self) -> list[str]:
        """The source split into lines."""
        return self._source.splitlines()

    @cached_property
    def comment_scan(self) -> CommentScan:
        """Every ``#`` comment, and whether the tokeniser read the whole file."""
        return collect_comments(source=self._source, source_lines=self.lines)

    @cached_property
    def docstrings(self) -> tuple[Docstring, ...]:
        """Every module, class and function docstring, in ``ast.walk`` order."""
        found: list[Docstring] = []
        for owner in self._index.collect(*_DOCSTRING_OWNERS):
            node = _find_docstring_node(owner)
            if node is not None:
                found.append(Docstring(owner=owner, node=node))
        return tuple(found)

    @cached_property
    def docstrings_by_owner(self) -> dict[int, Docstring]:
        """The docstrings keyed by the ``id`` of the node that owns them."""
        return {id(docstring.owner): docstring for docstring in self.docstrings}

    @cached_property
    def imports(self) -> tuple[ImportedModule, ...]:
        """Every imported module, in ``ast.walk`` order."""
        return tuple(
            entry
            for node in self._index.collect(ast.Import, ast.ImportFrom)
            for entry in _list_imported_modules(node)
        )


__all__ = ["Comment", "Docstring", "DocstringOwner", "ImportedModule", "SourceViews"]
