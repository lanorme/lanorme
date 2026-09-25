"""Tests for the function-body helpers the clone checks share."""

from __future__ import annotations

import ast

from lanorme.function_body import collect_local_bindings, list_body_statements


def _parse_function(source: str) -> ast.FunctionDef:
    return ast.parse(source).body[0]


def test_bindings_cover_the_functions_own_scope():
    # Arrange: parameters, targets, a walrus, an except alias, a nested def.
    func = _parse_function(
        "def outer(a, *rest, key=None, **extra):\n"
        "    total = 0\n"
        "    for item in rest:\n"
        "        total += item\n"
        "    if (found := extra.get(key)):\n"
        "        pass\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError as failure:\n"
        "        raise failure\n"
        "    def inner():\n"
        "        pass\n"
        "    return [n for n in rest]\n",
    )

    # Act.
    bound = collect_local_bindings(func=func)

    # Assert.
    assert bound == {"a", "rest", "key", "extra", "total", "item", "found", "failure", "inner", "n"}


def test_keyword_only_parameter_without_default_is_bound():
    # Arrange: ``kw_defaults`` holds None for a keyword-only parameter with no default,
    # and a default expression that binds a name through a walrus.
    func = _parse_function(
        "def handler(*, required, optional=(cached := 1)):\n    return required + optional\n",
    )

    # Act.
    bound = collect_local_bindings(func=func)

    # Assert: no crash on the None, and every parameter is bound.
    assert bound >= {"required", "optional"}


def test_nested_scopes_bind_their_own_names_only():
    # Arrange: a nested def declares nonlocal and binds a name; a lambda binds one.
    func = _parse_function(
        "def outer():\n"
        "    handler = make()\n"
        "    def inner(shadow):\n"
        "        nonlocal handler\n"
        "        handler = None\n"
        "        local = 1\n"
        "    sorter = lambda len: len\n"
        "    return handler\n",
    )

    # Act.
    bound = collect_local_bindings(func=func)

    # Assert: the outer scope keeps handler; inner's names are not its own.
    assert bound == {"handler", "inner", "sorter"}


def test_global_and_import_are_not_local_bindings():
    # Arrange.
    func = _parse_function(
        "def outer():\n"
        "    global counter\n"
        "    counter = 1\n"
        "    from shutil import copy\n"
        "    return copy\n",
    )

    # Act.
    bound = collect_local_bindings(func=func)

    # Assert: a global is bound elsewhere; an import names a fixed function.
    assert bound == set()


def test_body_statements_leave_out_the_docstring():
    # Arrange.
    func = _parse_function('def f():\n    """Doc."""\n    x = 1\n    return x\n')

    # Act.
    body = list_body_statements(func=func)

    # Assert.
    assert [type(node).__name__ for node in body] == ["Assign", "Return"]
