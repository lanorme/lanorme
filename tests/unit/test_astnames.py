"""The shared AST name readers in ``lanorme.astnames``."""

from __future__ import annotations

import ast

import pytest

from lanorme.astnames import (
    build_attr_chain,
    find_decorator_leaf,
    list_decorator_leaves,
    read_str_constant,
)

_DECORATED = """
@name
@a.b.attr
@call()
@a.called(1)()
@sub[int]
@(lambda f: f)
def f(): ...
"""


@pytest.fixture
def function() -> ast.FunctionDef:
    node = ast.parse(_DECORATED).body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


def test_leaves_look_through_calls_by_default(function: ast.FunctionDef) -> None:
    # Arrange / Act
    leaves = list_decorator_leaves(function)

    # Assert
    assert leaves == ["name", "attr", "call", "called", None, None]


def test_leaves_without_calls_or_with_subscripts(function: ast.FunctionDef) -> None:
    # Arrange / Act
    bare = list_decorator_leaves(function, calls=False)
    subscripted = list_decorator_leaves(function, subscripts=True)

    # Assert
    assert bare == ["name", "attr", None, None, None, None]
    assert subscripted == ["name", "attr", "call", "called", "sub", None]


def test_find_decorator_leaf_on_a_single_expression() -> None:
    # Arrange
    expression = ast.parse("pkg.mod.Thing(x)", mode="eval").body

    # Act / Assert
    assert find_decorator_leaf(expression) == "Thing"
    assert find_decorator_leaf(expression, calls=False) is None


@pytest.mark.parametrize(
    ("source", "chain"),
    [
        ("hashlib.md5", ("hashlib", "md5")),
        ("client.x.execute", ("client", "x", "execute")),
        ("name", ("name",)),
        ("a().b", ()),
        ("a[0].b", ()),
    ],
)
def test_build_attr_chain(source: str, chain: tuple[str, ...]) -> None:
    # Arrange
    node = ast.parse(source, mode="eval").body

    # Act / Assert
    assert build_attr_chain(node) == chain


def test_build_attr_chain_of_nothing() -> None:
    assert build_attr_chain(None) == ()


@pytest.mark.parametrize(
    ("source", "value"),
    [("'text'", "text"), ("b'bytes'", None), ("3", None), ("f'x{y}'", None), ("name", None)],
)
def test_read_str_constant(source: str, value: str | None) -> None:
    # Arrange
    node = ast.parse(source, mode="eval").body

    # Act / Assert
    assert read_str_constant(node) == value
