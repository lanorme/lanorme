"""Tests for the AST shapes shared by the naming checks.

What counts as a definition, what a body says about acting versus answering,
and which names a convention fixed before the author got to them.
"""

from __future__ import annotations

import ast

import pytest

from lanorme.checks.naming_shapes import (
    Definition,
    decorator_leaves,
    has_opaque_decorator,
    is_command,
    is_exempt,
    is_framework_named,
    is_raiser,
    iter_definitions,
    name_setting,
)


def _function(source: str) -> ast.FunctionDef:
    """The first function defined in *source*."""
    tree = ast.parse(source)
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))


def _definition(source: str) -> Definition:
    """The first definition ``iter_definitions`` yields for *source*."""
    return next(iter_definitions(tree=ast.parse(source)))


# --------------------------------------------------------------------------- #
# Which definitions count
# --------------------------------------------------------------------------- #


def test_iter_definitions_yields_top_level_and_methods_but_not_closures() -> None:
    # Arrange: a module with every placement a definition can have.
    tree = ast.parse(
        "def top():\n"
        "    def inner():\n"
        "        pass\n"
        "    return inner\n"
        "class Klass:\n"
        "    def method(self):\n"
        "        pass\n"
        "if TYPE_CHECKING:\n"
        "    def guarded():\n"
        "        pass\n"
        "match sys.platform:\n"
        "    case 'win32':\n"
        "        def matched():\n"
        "            pass\n"
        "for _ in range(1):\n"
        "    def looped():\n"
        "        pass\n"
    )

    # Act
    definitions = {d.name: d for d in iter_definitions(tree=tree)}

    # Assert: closures are absent, guarded definitions present, owners recorded.
    assert set(definitions) == {"top", "Klass", "method", "guarded", "matched", "looped"}
    assert definitions["method"].owner is not None and definitions["method"].owner.name == "Klass"
    assert definitions["top"].owner is None


def test_may_override_needs_a_base_class() -> None:
    plain = _definition("class A:\n    pass\n")
    derived = _definition("class B(A):\n    pass\n")
    assert not plain.may_override and Definition(node=plain.node, owner=derived.node).may_override


# --------------------------------------------------------------------------- #
# Acting versus answering
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("    x.clear()\n", True),
        ("    x.clear()\n    return\n", True),
        ("    return None\n", False),
        ("    return 1\n", False),
        ("    yield 1\n", False),
        ("    x.clear()\n    raise KeyError(x)\n", False),
        ('    """Only a docstring."""\n', False),
        ("    ...\n", False),
        ("    pass\n", False),
        ("    def inner():\n        return 1\n    x.clear()\n", True),
        ("    class Inner:\n        def get(self):\n            return 1\n    x.clear()\n", True),
    ],
)
def test_is_command(body: str, expected: bool) -> None:
    assert is_command(node=_function(f"def f(x):\n{body}")) is expected


def test_is_raiser_looks_at_the_last_statement() -> None:
    assert is_raiser(node=_function("def f(x):\n    x.clear()\n    raise KeyError(x)\n"))
    assert not is_raiser(node=_function("def f(x):\n    raise KeyError(x)\n    x.clear()\n"))


# --------------------------------------------------------------------------- #
# Names a convention chose
# --------------------------------------------------------------------------- #


def test_decorator_leaves_resolve_calls_attributes_and_subscripts() -> None:
    node = _function('@app.route("/")\n@x.setter\n@property\n@deco[0]\n@(lambda f: f)\ndef f():\n    pass\n')
    assert decorator_leaves(node=node) == {"route", "setter", "property", "deco", ""}


@pytest.mark.parametrize("decorator", ["@abc.abstractmethod", "@typing.override", "@staticmethod"])
def test_transparent_decorators_are_transparent_when_dotted(decorator: str) -> None:
    assert not has_opaque_decorator(node=_function(f"{decorator}\ndef f():\n    pass\n"))


@pytest.mark.parametrize("decorator", ["@deco[0]", "@(lambda f: f)", "@a.b(c)(d)", "@x.setter"])
def test_other_decorator_shapes_are_opaque(decorator: str) -> None:
    assert has_opaque_decorator(node=_function(f"{decorator}\ndef f():\n    pass\n"))


@pytest.mark.parametrize(
    "name",
    ["__init__", "and_", "_repr_mimebundle_", "on_click", "_before_request", "pytest_configure",
     "from_dict", "to_json", "as_tuple", "with_capacity", "dict_to_rows", "main", "cli",
     "process_request", "_env_file_callback"],
)
def test_reserved_function_names(name: str) -> None:
    assert is_framework_named(definition=_definition(f"def {name}():\n    pass\n"))


@pytest.mark.parametrize("name", ["write_layout", "layout", "keys"])
def test_ordinary_module_functions_are_the_authors(name: str) -> None:
    assert not is_framework_named(definition=_definition(f"def {name}():\n    pass\n"))


def test_protocol_names_are_reserved_on_methods_only() -> None:
    method = list(iter_definitions(tree=ast.parse("class C:\n    def keys(self):\n        pass\n")))[1]
    assert is_framework_named(definition=method)


def test_registering_decorators_reserve_the_name_but_transparent_ones_do_not() -> None:
    routed = _definition('@app.route("/")\ndef index():\n    pass\n')
    static = _definition("@staticmethod\ndef index():\n    pass\n")
    assert is_framework_named(definition=routed) and not is_framework_named(definition=static)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


def test_name_setting_reads_a_list_of_names() -> None:
    assert name_setting(settings={"verbs": ["a", "b"]}, key="verbs") == ["a", "b"]
    assert name_setting(settings={}, key="verbs") is None


@pytest.mark.parametrize("value", ["a", ["a", 1], 3])
def test_name_setting_rejects_other_shapes(value: object) -> None:
    with pytest.raises(TypeError):
        name_setting(settings={"verbs": value}, key="verbs")


def test_name_setting_rejects_an_entry_that_is_not_one_name() -> None:
    with pytest.raises(ValueError):
        name_setting(settings={"verbs": ["frob nicate"]}, key="verbs")


def test_exempt_matches_with_or_without_leading_underscores() -> None:
    assert is_exempt(name="_cert_verify", exempt=frozenset({"cert_verify"}))
    assert is_exempt(name="_cert_verify", exempt=frozenset({"_cert_verify"}))
    assert not is_exempt(name="cert_verify_all", exempt=frozenset({"cert_verify"}))
