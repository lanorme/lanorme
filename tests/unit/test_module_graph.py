"""Tests for lanorme.module_graph: dotted names, import resolution, cycles and shims."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lanorme.module_graph import ModuleGraph, build_module_graph, is_reexport_only
from lanorme.scan import Scan
from lanorme.sources import iter_parsed_modules


def build_graph(root: Path, files: dict[str, str]) -> ModuleGraph:
    """Write *files* under *root* and build the graph of what parses."""
    for relative, body in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    with Scan(root=root).activate():
        return build_module_graph(modules=iter_parsed_modules(root))


def build_cycle_graph(edges: dict[str, set[str]]) -> ModuleGraph:
    """A graph holding only the given import edges."""
    importers: dict[str, set[str]] = {}
    for source, targets in edges.items():
        for target in targets:
            importers.setdefault(target, set()).add(source)
    return ModuleGraph(
        nodes={},
        packages=frozenset(),
        importers={target: frozenset(found) for target, found in importers.items()},
        imports={source: frozenset(found) for source, found in edges.items()},
    )


@pytest.mark.parametrize(
    ("relative", "root", "dotted"),
    [
        ("src/lanorme/x.py", "src", "lanorme.x"),
        ("src/lanorme/__init__.py", "src", "lanorme"),
        ("app/domain/pii.py", "", "app.domain.pii"),
        ("script.py", "", "script"),
    ],
)
def test_dotted_names_climb_through_packages(tmp_path: Path, relative: str, root: str, dotted: str):
    # Arrange
    files = {
        "src/lanorme/__init__.py": "",
        "src/lanorme/x.py": "",
        "app/__init__.py": "",
        "app/domain/__init__.py": "",
        "app/domain/pii.py": "",
        "script.py": "",
    }

    # Act
    graph = build_graph(tmp_path, files)

    # Assert
    node = graph.nodes[relative]
    assert (node.root, node.dotted) == (root, dotted)
    assert graph.packages == frozenset({"src/lanorme", "app", "app/domain"})


@pytest.mark.parametrize(
    ("statement", "target"),
    [
        ("from . import b", "p/q/r/b.py"),
        ("from .. import c", "p/q/c.py"),
        ("from ... import d", "p/d.py"),
        ("from ..c import VALUE", "p/q/c.py"),
    ],
)
def test_relative_imports_resolve_by_level(tmp_path: Path, statement: str, target: str):
    # Arrange
    files = {
        "p/__init__.py": "",
        "p/d.py": "",
        "p/q/__init__.py": "",
        "p/q/c.py": "",
        "p/q/r/__init__.py": "",
        "p/q/r/b.py": "",
        "p/q/r/a.py": f"{statement}\n",
    }

    # Act
    graph = build_graph(tmp_path, files)

    # Assert
    assert target in graph.imports["p/q/r/a.py"]
    assert "p/q/r/a.py" in graph.find_importers(target)


def test_a_relative_import_above_the_root_is_unresolved(tmp_path: Path):
    # Arrange
    files = {"p/__init__.py": "", "p/a.py": "from ... import b\n", "b.py": ""}

    # Act
    graph = build_graph(tmp_path, files)

    # Assert
    assert graph.imports.get("p/a.py", frozenset()) == frozenset()


@pytest.mark.parametrize(
    ("files", "importer"),
    [
        ({"p/__init__.py": "", "p/a.py": "from .. import b\n", "b.py": ""}, "p/a.py"),
        ({"script.py": "from . import b\n", "b.py": ""}, "script.py"),
    ],
    ids=["two dots from a top-level package", "one dot from a top-level module"],
)
def test_a_relative_import_to_or_above_the_top_level_is_unresolved(
    tmp_path: Path,
    files: dict[str, str],
    importer: str,
):
    # Arrange: Python raises ImportError on both, so no edge may be invented.

    # Act
    graph = build_graph(tmp_path, files)

    # Assert
    assert "b.py" not in graph.imports.get(importer, frozenset())


def test_a_relative_import_inside_the_top_level_package_resolves(tmp_path: Path):
    # Arrange: the boundary's other side: one dot from the package's own __init__.py.
    files = {"p/__init__.py": "from . import b\n", "p/b.py": ""}

    # Act
    graph = build_graph(tmp_path, files)

    # Assert
    assert graph.imports["p/__init__.py"] == frozenset({"p/b.py"})


def test_an_import_runs_the_target_ancestors_but_not_the_importers_own(tmp_path: Path):
    # Arrange
    files = {
        "app/__init__.py": "",
        "app/pkg/__init__.py": "",
        "app/pkg/m.py": "from app.other.x import X\n",
        "app/other/__init__.py": "",
        "app/other/x.py": "X = 1\n",
    }

    # Act
    graph = build_graph(tmp_path, files)

    # Assert: app/other/__init__.py runs first; app/__init__.py is already loading.
    assert graph.imports["app/pkg/m.py"] == frozenset({"app/other/x.py", "app/other/__init__.py"})


def test_regression_a_cycle_through_an_ancestor_package_is_found(tmp_path: Path):
    # Arrange: m1 imports app.other.x; app/other/__init__.py imports m2.
    files = {
        "app/__init__.py": "",
        "app/pkg/__init__.py": "",
        "app/pkg/m1.py": "from app.other.x import X\n",
        "app/pkg/m2.py": "Y = 2\n",
        "app/other/__init__.py": "from app.pkg.m2 import Y\n",
        "app/other/x.py": "X = 1\n",
    }
    graph = build_graph(tmp_path, files)

    # Act
    found = graph.find_cycle_through(
        group=frozenset({"app/pkg/__init__.py", "app/pkg/m1.py", "app/pkg/m2.py"}),
    )

    # Assert
    assert found is not None


def test_from_package_import_submodule_records_both(tmp_path: Path):
    # Arrange
    files = {"pkg/__init__.py": "", "pkg/sub.py": "", "user.py": "from pkg import sub\n"}

    # Act
    graph = build_graph(tmp_path, files)

    # Assert
    assert graph.imports["user.py"] == frozenset({"pkg/sub.py", "pkg/__init__.py"})


def test_a_dotted_import_resolves_to_its_longest_known_prefix(tmp_path: Path):
    # Arrange: a.b exists, a.b.c does not.
    files = {"a/__init__.py": "", "a/b.py": "", "user.py": "import a.b.c\n"}

    # Act
    graph = build_graph(tmp_path, files)

    # Assert: a/__init__.py runs on the way to a.b, so it is an edge too.
    assert graph.imports["user.py"] == frozenset({"a/b.py", "a/__init__.py"})


def test_a_star_import_names_no_submodule(tmp_path: Path):
    # Arrange
    files = {"pkg/__init__.py": "", "pkg/star.py": "", "user.py": "from pkg import *\n"}

    # Act
    graph = build_graph(tmp_path, files)

    # Assert
    assert graph.imports["user.py"] == frozenset({"pkg/__init__.py"})


def test_type_checking_and_function_body_imports_count(tmp_path: Path):
    # Arrange
    user = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    import a\n"
        "def run():\n    import b\n    return b\n"
    )
    files = {"a.py": "", "b.py": "", "user.py": user}

    # Act
    graph = build_graph(tmp_path, files)

    # Assert
    assert graph.imports["user.py"] == frozenset({"a.py", "b.py"})


@pytest.mark.parametrize(("roots", "resolved"), [(("src",), True), (("one", "two"), False)])
def test_an_absolute_import_falls_back_to_exactly_one_other_root(
    tmp_path: Path,
    roots: tuple[str, ...],
    resolved: bool,
):
    # Arrange: tests/ is its own root; the name lives under one or two others.
    files = {"tests/test_x.py": "from app import x\n"}
    for root in roots:
        files[f"{root}/app/__init__.py"] = ""
        files[f"{root}/app/x.py"] = ""

    # Act
    graph = build_graph(tmp_path, files)

    # Assert
    assert bool(graph.imports.get("tests/test_x.py")) is resolved


def test_ambiguous_names_and_self_imports_get_no_edges(tmp_path: Path):
    # Arrange
    files = {
        "pkg/__init__.py": "",
        "pkg/a.py": "",
        "pkg/a/__init__.py": "",
        "pkg/b.py": "import pkg.b\n",
        "user.py": "import pkg.a\n",
    }

    # Act
    graph = build_graph(tmp_path, files)

    # Assert
    assert "pkg/a.py" not in graph.nodes and "pkg/a/__init__.py" not in graph.nodes
    assert graph.imports["user.py"] == frozenset({"pkg/__init__.py"})
    assert "pkg/b.py" not in graph.imports


@pytest.mark.parametrize(
    ("edges", "expected"),
    [
        ({"m1": {"out"}, "out": {"m2"}}, "out"),
        ({"m1": {"m2"}, "out": {"m1"}}, None),
        ({"m1": {"out"}, "out": {"m1"}}, None),
        ({"m1": {"out"}, "out": {"m2"}, "m2": {"m1"}}, None),
        ({"m1": {"out"}, "out": {"m1", "m2"}}, "out"),
        ({"m1": {"out"}, "out": {"p/__init__.py"}}, None),
    ],
    ids=[
        "new cycle",
        "dag",
        "back to the same member",
        "already on one cycle",
        "a second member through a module already on a cycle",
        "a package file that imports nothing",
    ],
)
def test_find_cycle_through_reports_only_a_new_cycle(edges: dict[str, set[str]], expected):
    # Arrange
    graph = build_cycle_graph(edges)

    # Act
    found = graph.find_cycle_through(group=frozenset({"m1", "m2", "p/__init__.py"}))

    # Assert
    assert found == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('"""Docstring."""\nimport os\nfrom a import b\n', True),
        ("__all__ = ['x']\n__all__ += ['y']\n__version__: str = '1'\n", True),
        ("from x import f\n__getattr__ = f('old')\n", True),
        ("def __getattr__(name):\n    return name\n", True),
        ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import a\n", True),
        ("try:\n    import a\nexcept ImportError:\n    a = None\n", False),
        ("try:\n    import a\nexcept ImportError:\n    pass\n", True),
        ("", True),
        ("def helper():\n    return 1\n", False),
        ("X = 1\n", False),
        ("class Thing:\n    pass\n", False),
    ],
)
def test_is_reexport_only(source: str, expected: bool):
    # Arrange
    tree = ast.parse(source)

    # Act
    found = is_reexport_only(tree=tree)

    # Assert
    assert found is expected
