"""The lazily cached views on ``Module``: lines, comments, docstrings, imports.

Each view is computed once per file per run and shared by every check that
reads it, and each must answer exactly what the walk it replaced answered.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lanorme.sources import Module, NodeIndex, UnparseableFile, parse_module

_SOURCE = '''"""Module doc."""
import a.b, c as see
from x.y import z, w as double
from . import sibling
from ..pkg import thing  # trailing note

text = "# not a comment"
# a standalone comment


class Holder:
    f"""not a docstring: an f-string"""

    def method(self):
        """
        Indented doc.
        """
        return 1


async def later():
    return 2
'''


@pytest.fixture
def module(tmp_path: Path) -> Module:
    path = tmp_path / "mod.py"
    path.write_text(_SOURCE, encoding="utf-8")
    parsed = parse_module(path, root=tmp_path)
    assert isinstance(parsed, Module)
    return parsed


def test_views_are_shared_by_every_parse_of_the_file(module: Module, tmp_path: Path) -> None:
    # Arrange / Act
    again = parse_module(tmp_path / "mod.py", root=tmp_path)

    # Assert: one computation per file, whichever check asks.
    assert again.views is module.views
    assert again.comments is module.comments
    assert again.lines is module.lines


def test_comments_come_from_tokens_only(module: Module) -> None:
    # Arrange / Act
    comments = [(c.line, c.column, c.token, c.text, c.standalone) for c in module.comments]

    # Assert: the '#' inside the string is not a comment.
    assert comments == [
        (5, 25, "# trailing note", "trailing note", False),
        (8, 0, "# a standalone comment", "a standalone comment", True),
    ]
    assert module.has_complete_comments


def test_a_source_the_tokeniser_rejects_keeps_the_prefix_and_says_so() -> None:
    # Arrange: a tree that parsed, paired with text tokenize gives up on.
    tree = ast.parse("x = 1\n")
    module = Module(
        path=Path("m.py"),
        relative="m.py",
        source="# before\nx = (\n",
        tree=tree,
        index=NodeIndex(tree),
    )

    # Act / Assert
    assert [c.text for c in module.comments] == ["before"]
    assert not module.has_complete_comments


def test_docstrings_match_ast_get_docstring(module: Module) -> None:
    # Arrange
    owners = module.index.collect(ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

    # Act
    found = {id(d.owner): d for d in module.docstrings}

    # Assert: the same owners, the same raw and cleaned text, in walk order.
    for owner in owners:
        raw = ast.get_docstring(owner, clean=False)
        docstring = module.find_docstring(owner)
        assert (docstring is None) == (raw is None)
        if docstring is not None:
            assert docstring.text == raw
            assert docstring.clean() == ast.get_docstring(owner)
            assert docstring.line == owner.body[0].lineno
    assert [type(d.owner).__name__ for d in module.docstrings] == ["Module", "FunctionDef"]
    assert len(found) == 2


def test_imports_list_each_named_module(module: Module) -> None:
    # Arrange / Act
    imports = [
        (i.is_from, i.module, i.level, [(a.name, a.asname) for a in i.aliases])
        for i in module.imports
    ]

    # Assert
    assert imports == [
        (False, "a.b", 0, [("a.b", None)]),
        (False, "c", 0, [("c", "see")]),
        (True, "x.y", 0, [("z", None), ("w", "double")]),
        (True, "", 1, [("sibling", None)]),
        (True, "pkg", 2, [("thing", None)]),
    ]


def test_an_unparseable_file_keeps_its_decoded_text(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "broken.py"
    path.write_text("def f(:\n    services.billing\n", encoding="utf-8")

    # Act
    parsed = parse_module(path, root=tmp_path)

    # Assert
    assert isinstance(parsed, UnparseableFile)
    assert "services.billing" in parsed.source


def test_an_undecodable_file_has_no_text(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "bad.py"
    path.write_bytes(b"x = '\xff'\n")

    # Act
    parsed = parse_module(path, root=tmp_path)

    # Assert
    assert isinstance(parsed, UnparseableFile)
    assert parsed.source == ""
