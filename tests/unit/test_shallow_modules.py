"""Tests for the shallow_modules check (SHALLOW-001): positives, boundaries and negatives.

Every tree is written under ``tmp_path`` and the check runs under an active
:class:`~lanorme.scan.Scan`. Unless a test says otherwise, a module at the
root imports every member, so each member is reachable. A sized member has an
exact number of SIZE-001 lines and of code lines: docstring lines count
towards the first and not the second.

The finding is anchored on ``<package>/__init__.py`` line 1, never on a member
module, so ``tests/unit/test_test_file_boundaries.py`` has no entry for it;
the test-file boundary is covered here (test packages, test modules inside a
package, and members imported only by tests).
"""

from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from lanorme import Violation, get_registry
from lanorme.checks.file_limits import FileLimitsCheck
from lanorme.checks.shallow_modules import ShallowModulesCheck
from lanorme.cli import _load_builtin_checks, main
from lanorme.scan import Scan

_SILENCE = (
    "If the split is deliberate, keep it and silence this with a per-file-ignores entry for "
    "'{package}/__init__.py', or '# noqa: SHALLOW-001' on its line 1 (which counts against a "
    "SUPPRESS-001 budget)."
)


def build_module_source(*, lines: int, code: int | None = None) -> str:
    """A module of exactly *lines* SIZE-001 lines, *code* of them outside the docstring."""
    code = lines if code is None else code
    padding = lines - code
    parts: list[str] = []
    if padding == 1:
        parts.append('"""Doc."""')
    elif padding >= 2:
        parts.extend(['"""Doc.', *["text"] * (padding - 2), '"""'])
    parts.extend(f"VALUE_{index} = {index}" for index in range(code))
    return "\n".join(parts) + "\n"


def write_tree(root: Path, files: dict[str, str]) -> None:
    """Write each relative path of *files* under *root*."""
    for relative, body in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def build_package_files(package: str, sizes: dict[str, int | tuple[int, int]]) -> dict[str, str]:
    """A package's files: an ``__init__.py`` per directory, its members, and a root importer.

    A size is the member's SIZE-001 lines, or a (lines, code lines) pair.
    """
    parts = package.split("/")
    files = {f"{'/'.join(parts[:depth])}/__init__.py": "" for depth in range(1, len(parts) + 1)}
    for stem, size in sizes.items():
        lines, code = size if isinstance(size, tuple) else (size, size)
        files[f"{package}/{stem}.py"] = build_module_source(lines=lines, code=code)
    names = ", ".join(sizes)
    files[f"uses_{package.replace('/', '_')}.py"] = (
        f"from {package.replace('/', '.')} import {names}\n"
    )
    return files


def run_shallow_check(root: Path, **settings: object) -> list[Violation]:
    """The SHALLOW-001 warnings on the tree at *root*, the check enabled and configured."""
    check = ShallowModulesCheck()
    check.configure(settings={"enabled": True, **settings})
    with Scan(root=root).activate() as scan:
        return check.check(scan).warnings


def list_flagged(root: Path, **settings: object) -> list[str]:
    """The files the warnings sit on."""
    return [warning.file for warning in run_shallow_check(root, **settings)]


# --------------------------------------------------------------------------- #
# Positive
# --------------------------------------------------------------------------- #


def test_three_small_members_give_one_warning_on_the_package_file(tmp_path: Path):
    # Arrange
    write_tree(tmp_path, build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10}))

    # Act
    warnings = run_shallow_check(tmp_path)

    # Assert
    assert [(w.code, w.file, w.line, w.column, w.scope) for w in warnings] == [
        ("SHALLOW-001", "pkg/inner/__init__.py", 1, None, "file"),
    ]


def test_regression_the_agent_written_domain_layer_reads_as_a_question(tmp_path: Path):
    # Arrange: the lanorme-2 stage 3 app/domain/ package, size for size.
    sizes = {
        "conversation": (17, 13),
        "errors": (18, 13),
        "guardrails": (10, 8),
        "pii": (75, 63),
        "policy": (15, 11),
        "topics": (17, 14),
    }
    write_tree(tmp_path, build_package_files("app/domain", sizes))

    # Act
    (warning,) = run_shallow_check(tmp_path)

    # Assert
    assert warning.message == (
        "Package 'app/domain/' holds 6 modules in 152 lines, 5 of them under 20 lines of code "
        "(conversation.py 17, errors.py 18, guardrails.py 10, pii.py 75, policy.py 15, "
        "topics.py 17). Is each of these really its own thing?"
    )
    assert warning.fix == (
        "If they belong together, consider folding conversation.py, errors.py, guardrails.py, "
        "policy.py and topics.py into 'app/domain/pii.py' (152 lines, under SIZE-001's "
        "300-line warning) and keeping the 'app/domain/' directory, because [layer_deps] "
        "layers names it. " + _SILENCE.format(package="app/domain")
    )


def test_a_plain_package_becomes_a_module_that_keeps_its_import_path(tmp_path: Path):
    # Arrange
    sizes = {"blocklist": 15, "events": 13, "pii": (87, 66), "tool_budget": (48, 36)}
    write_tree(tmp_path, build_package_files("app/guardrails", sizes))

    # Act
    (warning,) = run_shallow_check(tmp_path)

    # Assert
    assert warning.fix.startswith(
        "If they belong together, consider merging blocklist.py, events.py, pii.py and "
        "tool_budget.py into one module, 'app/guardrails.py' (163 lines, under SIZE-001's "
        "300-line warning), deleting 'app/guardrails/' and importing from 'app.guardrails' "
        "instead of 'app.guardrails.<module>'. ",
    )


@pytest.mark.parametrize(
    ("directory", "reason"),
    [
        ("domain", "[layer_deps] layers names it"),
        ("api", "[layer_deps] layers names it"),
        ("application/ports", "[port_coverage] ports_dir names it"),
        ("infrastructure/services", "[port_coverage] adapter_roots names it"),
        ("application/services", "NAMING-002 matches service modules under it"),
        ("infrastructure/repositories", "NAMING-001 matches repository modules under it"),
        ("api/v1/endpoints", "NAMING-003 matches endpoint modules under it"),
        ("application/commands", "TESTFILE-001 pairs its modules with tests"),
    ],
)
def test_rule_located_directories_fold_into_their_largest_member(
    tmp_path: Path,
    directory: str,
    reason: str,
):
    # Arrange
    write_tree(tmp_path, build_package_files(f"x/{directory}", {"a": 10, "big": 30, "c": 12}))

    # Act
    (warning,) = run_shallow_check(tmp_path)

    # Assert
    assert f"consider folding a.py and c.py into 'x/{directory}/big.py'" in warning.fix
    assert f"keeping the 'x/{directory}/' directory, because {reason}." in warning.fix


def test_the_ports_target_avoids_a_port_file_without_an_adapter(tmp_path: Path):
    # Arrange: unit_of_work.py is the largest, but PORT-002 expects no adapter for it.
    sizes = {"unit_of_work": 30, "chat_agent": 12, "policy_repository": 10}
    write_tree(tmp_path, build_package_files("x/application/ports", sizes))

    # Act
    (warning,) = run_shallow_check(tmp_path)

    # Assert
    assert "into 'x/application/ports/chat_agent.py'" in warning.fix


def test_a_junk_named_package_merges_into_a_module_named_for_its_contents(tmp_path: Path):
    # Arrange
    write_tree(tmp_path, build_package_files("pkg/utils", {"a": 10, "b": 10, "c": 10}))

    # Act
    (warning,) = run_shallow_check(tmp_path)

    # Assert
    assert "into one module in 'pkg/' named for what it holds rather than 'utils.py'" in warning.fix


def test_a_top_level_package_folds_inside(tmp_path: Path):
    # Arrange
    write_tree(tmp_path, build_package_files("toplevel", {"a": 10, "b": 20, "c": 10}))

    # Act
    (warning,) = run_shallow_check(tmp_path)

    # Assert
    assert "into 'toplevel/b.py'" in warning.fix
    assert "because it is a top-level package, the name installers and entry points use" in (
        warning.fix
    )


def test_a_package_beside_a_module_of_its_name_folds_inside(tmp_path: Path):
    # Arrange
    files = build_package_files("app/parts", {"a": 10, "b": 20, "c": 10})
    files["app/parts.py"] = "VALUE = 1\n"
    write_tree(tmp_path, files)

    # Act
    (warning,) = run_shallow_check(tmp_path)

    # Assert
    assert "into 'app/parts/b.py'" in warning.fix
    assert "because 'app/parts.py' already exists beside it." in warning.fix


def test_an_init_with_code_is_a_member_listed_first(tmp_path: Path):
    # Arrange
    files = build_package_files("x/domain", {"a": 10, "b": 20})
    files["x/domain/__init__.py"] = "def build():\n    return 1\n"
    write_tree(tmp_path, files)

    # Act
    (warning,) = run_shallow_check(tmp_path)

    # Assert
    assert "(__init__.py 2, a.py 10, b.py 20)" in warning.message
    assert "folding a.py and the code in '__init__.py' into 'x/domain/b.py'" in warning.fix


def test_relative_imports_make_members_reachable(tmp_path: Path):
    # Arrange: a from a sibling, b through ``from .inner.b``, c from a two-dot import.
    files = build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10})
    del files["uses_pkg_inner.py"]
    files["pkg/user.py"] = "from .inner import a\nfrom .inner.b import VALUE_0\n"
    files["pkg/inner/a.py"] = "from ..inner import c\n" + files["pkg/inner/a.py"]
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == ["pkg/inner/__init__.py"]


def test_a_src_layout_member_imported_from_src_fires(tmp_path: Path):
    # Arrange: a production importer under src/ and a test importer at the root.
    files = build_package_files("app/lib", {"a": 10, "b": 10, "c": 10})
    write_tree(tmp_path / "src", files)
    write_tree(tmp_path, {"tests/test_lib.py": "from app.lib import a\n"})

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == ["src/app/lib/__init__.py"]


@pytest.mark.parametrize("body", ["", '"""Only a docstring."""\n'])
def test_regression_an_empty_subpackage_does_not_hide_the_package(tmp_path: Path, body: str):
    # Arrange
    files = build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10})
    files["pkg/inner/sub/__init__.py"] = body
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == ["pkg/inner/__init__.py"]


# --------------------------------------------------------------------------- #
# Boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("members", "settings", "fires"),
    [(3, {}, True), (2, {}, False), (2, {"min_modules": 2}, True)],
)
def test_min_modules_is_inclusive(tmp_path: Path, members: int, settings: dict, fires: bool):
    # Arrange
    sizes = {f"m{index}": 10 for index in range(members)}
    write_tree(tmp_path, build_package_files("pkg/inner", sizes))

    # Act
    flagged = list_flagged(tmp_path, **settings)

    # Assert
    assert bool(flagged) is fires


@pytest.mark.parametrize(("total", "fires"), [(200, True), (201, False)])
def test_the_merge_budget_counts_size_001_lines(tmp_path: Path, total: int, fires: bool):
    # Arrange: blank and comment lines do not count; docstring lines do.
    files = build_package_files("pkg/inner", {"a": 10, "b": 10, "c": (total - 20, 15)})
    files["pkg/inner/a.py"] += "\n\n# a comment\n# another\n"
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert bool(flagged) is fires


@pytest.mark.parametrize(("code", "fires"), [(19, True), (20, False)])
def test_a_member_under_twenty_lines_of_code_is_tiny(tmp_path: Path, code: int, fires: bool):
    # Arrange: one tiny member and two at the boundary, docstrings padding them.
    sizes = {"a": 5, "b": (code + 10, code), "c": (code + 10, code)}
    write_tree(tmp_path, build_package_files("pkg/inner", sizes))

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert bool(flagged) is fires


@pytest.mark.parametrize(
    ("sizes", "fires"),
    [({"a": 5, "b": 5, "c": 30, "d": 30}, True), ({"a": 5, "b": 30, "c": 30}, False)],
)
def test_exactly_half_tiny_is_enough(tmp_path: Path, sizes: dict[str, int], fires: bool):
    # Arrange
    write_tree(tmp_path, build_package_files("pkg/inner", sizes))

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert bool(flagged) is fires


@pytest.mark.parametrize(("relatives", "fires"), [(1, True), (2, False)])
def test_a_layout_repeated_by_two_other_packages_is_exempt(
    tmp_path: Path,
    relatives: int,
    fires: bool,
):
    # Arrange: other packages share two of the three module names, with code
    # big enough that they are never reported themselves.
    files = build_package_files("one/feature", {"alpha": 10, "beta": 10, "gamma": 10})
    for index in range(relatives):
        files.update(build_package_files(f"other{index}/feature", {"alpha": 150, "beta": 150}))
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert ("one/feature/__init__.py" in flagged) is fires


@pytest.mark.parametrize(
    ("settings", "total", "fires"),
    [
        ({"file_warn_lines": 150}, 100, True),
        ({"file_warn_lines": 150}, 101, False),
        ({"file_error_lines": 240}, 160, True),
        ({"file_error_lines": 240}, 161, False),
    ],
)
def test_the_budget_is_two_thirds_of_the_effective_warning(
    tmp_path: Path,
    settings: dict,
    total: int,
    fires: bool,
):
    # Arrange
    write_tree(tmp_path, build_package_files("pkg/inner", {"a": 5, "b": 5, "c": total - 10}))

    # Act
    warnings = run_shallow_check(tmp_path, **settings)

    # Assert
    assert bool(warnings) is fires
    if fires:
        warning_line = min(
            settings.get("file_warn_lines", 300),
            settings.get("file_error_lines", 500),
        )
        assert f"under SIZE-001's {warning_line}-line warning" in warnings[0].fix


# --------------------------------------------------------------------------- #
# Negative
# --------------------------------------------------------------------------- #


def test_a_package_with_code_in_a_subpackage_is_not_a_leaf(tmp_path: Path):
    # Arrange
    files = build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10})
    files["pkg/inner/sub/__init__.py"] = ""
    files["pkg/inner/sub/one.py"] = "VALUE = 1\n"
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert "pkg/inner/__init__.py" not in flagged


@pytest.mark.parametrize("package", ["app/migrations", "app/alembic/versions", "tests/helpers"])
def test_migrations_alembic_and_test_packages_are_skipped(tmp_path: Path, package: str):
    # Arrange
    write_tree(tmp_path, build_package_files(package, {"a": 10, "b": 10, "c": 10}))

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


def test_a_test_module_inside_a_package_is_not_a_member(tmp_path: Path):
    # Arrange: two members and a test module, below min_modules.
    files = build_package_files("pkg/inner", {"a": 10, "b": 10})
    files["pkg/inner/test_a.py"] = "from pkg.inner import a\n"
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


@pytest.mark.parametrize("importer", ["tests/test_inner.py", None])
def test_a_member_only_tests_import_or_nothing_imports_is_loaded_by_name(
    tmp_path: Path,
    importer: str | None,
):
    # Arrange
    files = build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10})
    files["uses_pkg_inner.py"] = "from pkg.inner import a, b\n"
    if importer:
        files[importer] = "from pkg.inner import c\n"
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


@pytest.mark.parametrize("stem", ["admin", "apps", "asgi", "settings", "tasks", "urls", "wsgi"])
def test_a_framework_loaded_module_exempts_the_package(tmp_path: Path, stem: str):
    # Arrange
    write_tree(tmp_path, build_package_files("pkg/inner", {"a": 10, "b": 10, stem: 10}))

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("schema_pb2.py", "VALUE = 1\n"),
        ("_version.py", "VERSION = '1'\n"),
        ("generated.py", "# Code generated by a tool. DO NOT EDIT.\nVALUE = 1\n"),
    ],
)
def test_generated_code_exempts_the_package(tmp_path: Path, name: str, body: str):
    # Arrange
    files = build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10})
    files[f"pkg/inner/{name}"] = body
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


@pytest.mark.parametrize(
    "shim",
    [
        "from .a import *\n",
        "import warnings\n\ndef __getattr__(name):\n    warnings.warn(name)\n    return name\n",
        "__all__ = ['a']\n__version__ = '1'\n__all__ += ['b']\n",
    ],
)
def test_a_reexport_shim_is_not_a_member(tmp_path: Path, shim: str):
    # Arrange: two members and a shim, below min_modules.
    files = build_package_files("pkg/inner", {"a": 10, "b": 10})
    files["pkg/inner/compat.py"] = shim
    files["uses_pkg_inner.py"] += "from pkg.inner import compat\n"
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


@pytest.mark.parametrize(
    ("settings", "wiring"),
    [
        ({}, "x/api/dependencies.py"),
        ({"port_composition_root": ["*wiring.py"]}, "x/api/wiring.py"),
    ],
)
def test_a_composition_root_is_not_a_member(tmp_path: Path, settings: dict, wiring: str):
    # Arrange: two members and a wiring module, below min_modules.
    files = build_package_files("x/api", {"a": 10, "b": 10})
    files[wiring] = "VALUE = 1\n"
    files["uses_x_api.py"] += f"import {wiring.removesuffix('.py').replace('/', '.')}\n"
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path, **settings)

    # Assert
    assert flagged == []


@pytest.mark.parametrize("name", ["__main__.py", "manage.py", "setup.py", "0001_initial.py"])
def test_scripts_and_unimportable_names_are_not_members(tmp_path: Path, name: str):
    # Arrange
    files = build_package_files("pkg/inner", {"a": 10, "b": 10})
    files[f"pkg/inner/{name}"] = "VALUE = 1\n"
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


def test_a_namespace_package_is_never_judged(tmp_path: Path):
    # Arrange
    files = build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10})
    del files["pkg/inner/__init__.py"]
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


def test_an_ambiguous_member_name_is_left_unresolved(tmp_path: Path):
    # Arrange: pkg/inner/a.py and pkg/inner/a/__init__.py both claim pkg.inner.a.
    files = build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10})
    files["pkg/inner/a/__init__.py"] = ""
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


def test_a_name_two_other_roots_define_stays_unresolved(tmp_path: Path):
    # Arrange: one/app/x and two/app/x both hold app.x.c; a third root imports it.
    files = build_package_files("app/x", {"a": 10, "b": 10, "c": 10})
    importer = files.pop("uses_app_x.py")
    write_tree(tmp_path / "one", files)
    write_tree(tmp_path / "two", files)
    write_tree(tmp_path / "three", {"uses.py": importer})

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


def test_a_merge_that_closes_a_new_import_cycle_is_not_proposed(tmp_path: Path):
    # Arrange: a imports outside, which imports b; b does not reach a.
    files = build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10})
    files["pkg/inner/a.py"] = "from pkg import outside\n" + files["pkg/inner/a.py"]
    files["pkg/outside.py"] = "import pkg.inner.b\n"
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


def test_regression_an_existing_cycle_through_one_member_still_fires(tmp_path: Path):
    # Arrange: a imports outside, which imports a back; the merge adds no cycle.
    # (``import pkg.inner.a`` names a alone; ``from pkg.inner import a`` would
    # also name the package's ``__init__.py``, another module of the group.)
    files = build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10})
    files["pkg/inner/a.py"] = "from pkg import outside\n" + files["pkg/inner/a.py"]
    files["pkg/outside.py"] = "import pkg.inner.a\n"
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == ["pkg/inner/__init__.py"]


@pytest.mark.parametrize("broken", ["pkg/inner/broken.py", "pkg/inner/sub/broken.py"])
def test_an_unparseable_file_skips_the_package_without_raising(tmp_path: Path, broken: str):
    # Arrange
    files = build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10})
    files[broken] = "def broken(:\n"
    if "/sub/" in broken:
        files["pkg/inner/sub/__init__.py"] = ""
    write_tree(tmp_path, files)

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


def test_the_check_is_off_by_default(tmp_path: Path):
    # Arrange
    write_tree(tmp_path, build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10}))

    # Act
    with Scan(root=tmp_path).activate() as scan:
        result = ShallowModulesCheck().check(scan)

    # Assert
    assert (result.violations, result.warnings) == ([], [])


# --------------------------------------------------------------------------- #
# Configuration, state and the CLI
# --------------------------------------------------------------------------- #


def run_cli(argv: list[str]) -> int:
    """Run ``lanorme`` with *argv* and return its exit code."""
    try:
        main(argv)
    except SystemExit as exit_signal:
        return int(exit_signal.code or 0)
    return 0


def read_ndjson_findings(out: str) -> list[dict]:
    """The findings of an ``--output-format ndjson`` run."""
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def write_project(root: Path, *, config: str, files: dict[str, str]) -> None:
    """A project with a ``pyproject.toml`` holding *config* and the given files."""
    write_tree(root, {"pyproject.toml": config, **files})


_ENABLED = "[tool.lanorme.shallow_modules]\nenabled = true\n"


@pytest.mark.parametrize(
    "table",
    [
        "enabled = true\nmin_module = 3\n",
        "min_modules = 1\n",
        'min_modules = "3"\n',
        'enabled = "yes"\n',
    ],
)
def test_a_bad_setting_is_a_config_error(tmp_path: Path, capsys, table: str):
    # Arrange
    write_project(tmp_path, config=f"[tool.lanorme.shallow_modules]\n{table}", files={"m.py": ""})

    # Act
    code = run_cli(["check", str(tmp_path)])

    # Assert
    assert code == 2
    assert "[tool.lanorme.shallow_modules]" in capsys.readouterr().err


def test_show_config_lists_the_nine_keys_and_not_the_scope(tmp_path: Path, capsys):
    # Arrange
    write_project(tmp_path, config=_ENABLED, files={"m.py": ""})

    # Act
    run_cli(["check", str(tmp_path), "--show-config"])
    out = capsys.readouterr().out

    # Assert
    summary = out.split("  shallow_modules", 1)[1].split("\n  ", 2)
    assert summary[0].lstrip().startswith("enabled=True min_modules=3 file_warn_lines=300")
    assert "scope" not in summary[0]
    assert summary[1].strip() == (
        "keys: adapter_roots, enabled, file_error_lines, file_warn_lines, "
        "layer_composition_root, layers, min_modules, port_composition_root, ports_dir"
    )


def test_the_registered_template_copies_and_runs_on_two_trees_independently(tmp_path: Path):
    # Arrange
    _load_builtin_checks()
    check = copy.deepcopy(get_registry()["shallow_modules"])
    check.configure(settings={"enabled": True})
    write_tree(tmp_path / "one", build_package_files("pkg/inner", {"a": 10, "b": 10, "c": 10}))
    write_tree(tmp_path / "two", build_package_files("pkg/inner", {"a": 10, "b": 10}))

    # Act
    with Scan(root=tmp_path / "one").activate() as scan:
        first = check.check(scan).warnings
    with Scan(root=tmp_path / "two").activate() as scan:
        second = check.check(scan).warnings

    # Assert
    assert [w.file for w in first] == ["pkg/inner/__init__.py"]
    assert second == []


@pytest.mark.parametrize(("target", "reported"), [("app/domain", True), ("app/api", False)])
def test_narrowing_to_a_subtree_keeps_the_package_finding(
    tmp_path: Path,
    capsys,
    target: str,
    reported: bool,
):
    # Arrange
    files = build_package_files("app/domain", {"a": 10, "b": 10, "c": 10})
    files.update(build_package_files("app/api", {"routes": 120}))
    write_project(tmp_path, config=_ENABLED, files=files)

    # Act
    run_cli(["check", str(tmp_path / target), "--select", "SHALLOW", "--output-format", "ndjson"])
    findings = read_ndjson_findings(capsys.readouterr().out)

    # Assert
    assert bool(findings) is reported


@pytest.mark.parametrize(
    ("config", "first_line", "expected"),
    [
        (_ENABLED, "", {("SHALLOW-001", "warning")}),
        (
            _ENABLED
            + '[tool.lanorme.per-file-ignores]\n"**/domain/__init__.py" = ["SHALLOW-001"]\n',
            "",
            set(),
        ),
        (_ENABLED, "# noqa: SHALLOW-001\n", set()),
        (
            '[tool.lanorme]\nextends = ["strict"]\n',
            "# noqa: SHALLOW-001\n",
            {("SUPPRESS-001", "error")},
        ),
        (
            '[tool.lanorme]\nextends = ["strict"]\n'
            '[tool.lanorme.per-file-ignores]\n"**/domain/__init__.py" = ["SHALLOW-001"]\n',
            "",
            set(),
        ),
    ],
    ids=[
        "reported",
        "per-file-ignores",
        "noqa",
        "noqa under a strict budget",
        "per-file-ignores strict",
    ],
)
def test_the_suggested_silencing_works_and_its_cost_under_strict(
    tmp_path: Path,
    capsys,
    config: str,
    first_line: str,
    expected: set[tuple[str, str]],
):
    # Arrange
    files = build_package_files("app/domain", {"a": 10, "b": 10, "c": 10})
    files["app/domain/__init__.py"] = first_line + '"""Domain."""\n'
    write_project(tmp_path, config=config, files=files)

    # Act
    run_cli(["check", str(tmp_path), "--select", "SHALLOW,SUPPRESS", "--output-format", "ndjson"])
    findings = read_ndjson_findings(capsys.readouterr().out)

    # Assert
    assert {(f["code"], f["severity"]) for f in findings} == expected


@pytest.mark.parametrize(
    ("config", "exit_code", "promoted"),
    [
        (_ENABLED, 0, False),
        ('[tool.lanorme]\nextends = ["strict"]\n', 0, False),
        (_ENABLED + '[tool.lanorme]\npromote = ["ALL"]\n', 0, False),
        (_ENABLED + '[tool.lanorme]\npromote = ["SHALLOW-001"]\n', 1, True),
        ('[tool.lanorme]\nextends = ["strict"]\npromote = ["SHALLOW"]\n', 1, True),
    ],
    ids=["enabled", "strict", "promote all", "promote the code", "strict and the category"],
)
def test_promote_all_leaves_it_a_warning_and_naming_it_promotes_it(
    tmp_path: Path,
    capsys,
    config: str,
    exit_code: int,
    promoted: bool,
):
    # Arrange
    files = build_package_files("app/domain", {"a": 10, "b": 10, "c": 10})
    write_project(tmp_path, config=config, files=files)

    # Act
    code = run_cli(["check", str(tmp_path), "--select", "SHALLOW", "--output-format", "ndjson"])
    (finding,) = read_ndjson_findings(capsys.readouterr().out)

    # Assert
    assert code == exit_code
    expected_severity = "error" if promoted else "warning"
    assert (finding["code"], finding["promoted"], finding["severity"]) == (
        "SHALLOW-001",
        promoted,
        expected_severity,
    )


# --------------------------------------------------------------------------- #
# Invariants: following the fix never trips SIZE-001 or SHALLOW-001 again
# --------------------------------------------------------------------------- #

# Generated trees put each package under app/; ``domain`` is a rule-located
# layer (fold inside), the others become ``app/<name>.py``.
_GENERATED_PACKAGES = ("domain", "alpha", "beta")
_WARNING_LINES = 300


@dataclass
class GeneratedPackage:
    """A generated package: each member's imports and body, by stem."""

    name: str
    imports: dict[str, list[str]] = field(default_factory=dict)
    bodies: dict[str, str] = field(default_factory=dict)


def generate_packages(seed: int) -> list[GeneratedPackage]:
    """One to three packages of two to seven members, 1 to 120 lines skewed small, importing at random."""
    rng = random.Random(seed)
    packages = [GeneratedPackage(name=name) for name in _GENERATED_PACKAGES[: rng.randint(1, 3)]]
    for package in packages:
        for index in range(rng.randint(2, 7)):
            lines = round(rng.triangular(1, 120, 1))
            package.bodies[f"m{index}"] = build_module_source(
                lines=lines,
                code=rng.randint(1, lines),
            )
            package.imports[f"m{index}"] = []
    for package in packages:
        for stem in package.bodies:
            other = rng.choice(packages)
            if other is not package and rng.random() < 0.3:
                package.imports[stem].append(
                    f"import app.{other.name}.{rng.choice(list(other.bodies))}",
                )
    return packages


def render_generated_tree(packages: list[GeneratedPackage]) -> dict[str, str]:
    """The files of the generated packages, with ``main.py`` importing every member."""
    files = {"app/__init__.py": ""}
    uses = []
    for package in packages:
        files[f"app/{package.name}/__init__.py"] = ""
        for stem, body in package.bodies.items():
            header = "".join(f"{line}\n" for line in package.imports[stem])
            files[f"app/{package.name}/{stem}.py"] = header + body
            uses.append(f"import app.{package.name}.{stem}\n")
    files["main.py"] = "".join(uses)
    return files


def apply_merge(files: dict[str, str], *, package: str, target: str) -> dict[str, str]:
    """Follow a fix: merge the members into *target*, drop their own imports, rewrite importers."""
    prefix = f"{package}/"
    dotted = package.replace("/", ".")
    target_dotted = target.removesuffix(".py").replace("/", ".")
    members = sorted(
        path for path in files if path.startswith(prefix) and not path.endswith("__init__.py")
    )
    merged = [
        line
        for path in members
        for line in files[path].splitlines()
        if not line.startswith(f"import {dotted}.")
    ]
    kept = {path: body for path, body in files.items() if path not in members}
    if not target.startswith(prefix):
        kept = {path: body for path, body in kept.items() if not path.startswith(prefix)}
    rewritten = {
        path: "\n".join(
            f"import {target_dotted}" if line.startswith(f"import {dotted}.") else line
            for line in body.splitlines()
        )
        + "\n"
        for path, body in kept.items()
    }
    rewritten[target] = "\n".join(merged) + "\n"
    return rewritten


def read_merge_target(fix: str) -> str:
    """The module a SHALLOW-001 fix names, when it names one."""
    if "consider folding" in fix:
        return fix.split(" into '", 1)[1].split("'", 1)[0]
    return fix.split("into one module, '", 1)[1].split("'", 1)[0]


def run_size_and_shallow(root: Path) -> list[Violation]:
    """Every SIZE-001 and SHALLOW-001 finding on the tree at *root*."""
    with Scan(root=root).activate() as scan:
        size = FileLimitsCheck().check(scan)
        shallow = ShallowModulesCheck(enabled=True).check(scan)
    findings = [*size.violations, *size.warnings, *shallow.warnings]
    return [finding for finding in findings if finding.code in {"SIZE-001", "SHALLOW-001"}]


@pytest.mark.parametrize("seed", range(60))
def test_property_following_the_fix_is_legal(tmp_path: Path, seed: int):
    # Arrange
    files = render_generated_tree(generate_packages(seed))
    write_tree(tmp_path / "before", files)

    # Act
    findings = run_size_and_shallow(tmp_path / "before")

    # Assert: each finding's merge stays under SIZE-001 and is never reported again.
    for finding in findings:
        assert finding.code == "SHALLOW-001", finding
        package = finding.file.removesuffix("/__init__.py")
        total = int(finding.message.split(" lines,", 1)[0].rsplit(" ", 1)[1])
        assert total < _WARNING_LINES
        target = read_merge_target(finding.fix)
        after = tmp_path / f"after_{package.replace('/', '_')}"
        write_tree(after, apply_merge(files, package=package, target=target))
        again = run_size_and_shallow(after)
        assert not [f for f in again if f.file in {finding.file, target}], again


@pytest.mark.parametrize("seed", range(20))
def test_property_a_split_size_001_asks_for_never_trips_shallow_001(tmp_path: Path, seed: int):
    # Arrange: a file of at least the warning, split into two to five parts.
    rng = random.Random(seed)
    lines = rng.randint(_WARNING_LINES, _WARNING_LINES + 120)
    parts = rng.randint(2, 5)
    sizes = {f"part{index}": lines // parts + (index < lines % parts) for index in range(parts)}
    write_tree(tmp_path, build_package_files("app/big", sizes))

    # Act
    flagged = list_flagged(tmp_path)

    # Assert
    assert flagged == []


# --------------------------------------------------------------------------- #
# Merge-move replica: a hexagonal app shaped like the lanorme-2 stage 3 run
# --------------------------------------------------------------------------- #

_REPLICA_CONFIG = '[tool.lanorme]\nextends = ["strict", "hexagonal"]\nsource_root = "app"\n'

_ERRORS = 'class PolicyViolationError(Exception):\n    """A message broke the tenant policy."""\n'
_POLICY = (
    "@dataclass(frozen=True)\n"
    "class Policy:\n"
    '    """What a tenant allows."""\n\n'
    "    blocked_words: tuple[str, ...] = ()\n\n"
    "    def is_allowed(self, *, message: str) -> bool:\n"
    '        """True when the message holds no blocked word."""\n'
    "        return not any(word in message for word in self.blocked_words)\n"
)
_CONVERSATION = (
    "@dataclass\n"
    "class Conversation:\n"
    '    """The messages of one session."""\n\n'
    "    messages: list[str] = field(default_factory=list)\n"
)
_CHAT_AGENT = (
    "class ChatAgent(Protocol):\n"
    '    """Replies to a message."""\n\n'
    "    def reply(self, *, message: str) -> str:\n"
    '        """The answer to the message."""\n'
    "        ...\n"
)
_CONVERSATION_REPOSITORY = (
    "class ConversationRepository(Protocol):\n"
    '    """Stores conversations by session."""\n\n'
    "    def get(self, *, session_id: str) -> Conversation:\n"
    '        """The conversation of a session."""\n'
    "        ...\n\n"
    "    def update(self, *, session_id: str, conversation: Conversation) -> None:\n"
    '        """Store the conversation of a session."""\n'
    "        ...\n"
)
_POLICY_REPOSITORY = (
    "class PolicyRepository(Protocol):\n"
    '    """Stores tenant policies."""\n\n'
    "    def get(self, *, tenant: str) -> Policy:\n"
    '        """The policy of a tenant."""\n'
    "        ...\n"
)
_CHAT_SERVICE = (
    "@dataclass\n"
    "class ChatService:\n"
    '    """Answers a message within the tenant policy."""\n\n'
    "    agent: ChatAgent\n"
    "    policies: PolicyRepository\n"
    "    conversations: ConversationRepository\n\n"
    "    def respond(self, *, tenant: str, session_id: str, message: str) -> str:\n"
    '        """The agent reply, once the policy allows the message."""\n'
    "        if not self.policies.get(tenant=tenant).is_allowed(message=message):\n"
    "            raise PolicyViolationError(message)\n"
    "        conversation = self.conversations.get(session_id=session_id)\n"
    "        conversation.messages.append(message)\n"
    "        self.conversations.update(session_id=session_id, conversation=conversation)\n"
    "        return self.agent.reply(message=message)\n"
)
_CONVERSATION_SERVICE = (
    "@dataclass\n"
    "class ConversationService:\n"
    '    """Reads session history."""\n\n'
    "    conversations: ConversationRepository\n\n"
    "    def count_messages(self, *, session_id: str) -> int:\n"
    '        """How many messages a session holds."""\n'
    "        return len(self.conversations.get(session_id=session_id).messages)\n"
)
_POLICY_SERVICE = (
    "@dataclass\n"
    "class PolicyService:\n"
    '    """Reads tenant policies."""\n\n'
    "    policies: PolicyRepository\n\n"
    "    def list_blocked_words(self, *, tenant: str) -> tuple[str, ...]:\n"
    '        """The words a tenant blocks."""\n'
    "        return self.policies.get(tenant=tenant).blocked_words\n"
)


def build_replica_tree(*, merged: bool) -> dict[str, str]:
    """The replica before the three merges, or after them (folded into the largest member)."""
    domain = "app.domain.policy" if merged else "app.domain.{}"
    ports = (
        "app.application.ports.conversation_repository" if merged else "app.application.ports.{}"
    )
    services = "app.application.services.chat" if merged else "app.application.services.{}"
    dataclass_import = "from dataclasses import dataclass, field\n"
    files = {
        "pyproject.toml": _REPLICA_CONFIG,
        "app/__init__.py": '"""Guarded agent service."""\n',
        "app/domain/__init__.py": '"""Domain model."""\n',
        "app/application/__init__.py": '"""Use cases."""\n',
        "app/application/ports/__init__.py": '"""Ports."""\n',
        "app/application/services/__init__.py": '"""Services."""\n',
        "app/infrastructure/__init__.py": '"""Adapters."""\n',
        "app/infrastructure/services/__init__.py": '"""Service adapters."""\n',
        "app/infrastructure/services/echo_agent.py": (
            f"from {ports.format('chat_agent')} import ChatAgent\n\n\n"
            "class EchoAgent(ChatAgent):\n"
            '    """Replies with the message."""\n\n'
            "    def reply(self, *, message: str) -> str:\n"
            '        """The message itself."""\n'
            "        return message\n"
        ),
        "app/main.py": (
            f"from {services.format('chat')} import ChatService\n"
            f"from {services.format('conversations')} import ConversationService\n"
            f"from {services.format('policies')} import PolicyService\n"
            "from app.infrastructure.services.echo_agent import EchoAgent\n\n"
            "SERVICES = (ChatService, ConversationService, PolicyService, EchoAgent)\n"
        ),
    }
    if merged:
        files["app/domain/policy.py"] = (
            f'"""The domain model."""\n\n{dataclass_import}\n\n{_ERRORS}\n\n{_POLICY}\n\n{_CONVERSATION}'
        )
        files["app/application/ports/conversation_repository.py"] = (
            '"""The ports."""\n\nfrom typing import Protocol\n\n'
            "from app.domain.policy import Conversation, Policy\n\n\n"
            f"{_CHAT_AGENT}\n\n{_CONVERSATION_REPOSITORY}\n\n{_POLICY_REPOSITORY}"
        )
        files["app/application/services/chat.py"] = (
            '"""The services."""\n\nfrom dataclasses import dataclass\n\n'
            f"from {ports} import ChatAgent, ConversationRepository, PolicyRepository\n"
            "from app.domain.policy import PolicyViolationError\n\n\n"
            f"{_CHAT_SERVICE}\n\n{_CONVERSATION_SERVICE}\n\n{_POLICY_SERVICE}"
        )
        return files
    files.update(
        {
            "app/domain/errors.py": f'"""Domain errors."""\n\n\n{_ERRORS}',
            "app/domain/policy.py": f'"""Tenant policy."""\n\n{dataclass_import}\n\n{_POLICY}',
            "app/domain/conversation.py": f'"""Session history."""\n\n{dataclass_import}\n\n{_CONVERSATION}',
            "app/application/ports/chat_agent.py": (
                f'"""The agent port."""\n\nfrom typing import Protocol\n\n\n{_CHAT_AGENT}'
            ),
            "app/application/ports/conversation_repository.py": (
                '"""The conversation port."""\n\nfrom typing import Protocol\n\n'
                "from app.domain.conversation import Conversation\n\n\n"
                f"{_CONVERSATION_REPOSITORY}"
            ),
            "app/application/ports/policy_repository.py": (
                '"""The policy port."""\n\nfrom typing import Protocol\n\n'
                f"from app.domain.policy import Policy\n\n\n{_POLICY_REPOSITORY}"
            ),
            "app/application/services/chat.py": (
                '"""The chat service."""\n\nfrom dataclasses import dataclass\n\n'
                "from app.application.ports.chat_agent import ChatAgent\n"
                "from app.application.ports.conversation_repository import ConversationRepository\n"
                "from app.application.ports.policy_repository import PolicyRepository\n"
                f"from app.domain.errors import PolicyViolationError\n\n\n{_CHAT_SERVICE}"
            ),
            "app/application/services/conversations.py": (
                '"""The conversation service."""\n\nfrom dataclasses import dataclass\n\n'
                "from app.application.ports.conversation_repository import ConversationRepository\n\n\n"
                f"{_CONVERSATION_SERVICE}"
            ),
            "app/application/services/policies.py": (
                '"""The policy service."""\n\nfrom dataclasses import dataclass\n\n'
                f"from app.application.ports.policy_repository import PolicyRepository\n\n\n{_POLICY_SERVICE}"
            ),
        },
    )
    return files


def read_replica_findings(root: Path, capsys) -> list[dict]:
    """Every finding of a strict, hexagonal run over the replica."""
    run_cli(["check", str(root), "--output-format", "ndjson"])
    return read_ndjson_findings(capsys.readouterr().out)


def test_merge_move_replica_follows_every_fix_without_new_findings(tmp_path: Path, capsys):
    # Arrange
    write_tree(tmp_path / "before", build_replica_tree(merged=False))
    write_tree(tmp_path / "after", build_replica_tree(merged=True))

    # Act
    before = read_replica_findings(tmp_path / "before", capsys)
    after = read_replica_findings(tmp_path / "after", capsys)

    # Assert: the three packages fold into the targets the after tree uses.
    shallow = sorted(
        (f["file"], read_merge_target(f["fix"])) for f in before if f["code"] == "SHALLOW-001"
    )
    assert shallow == [
        ("app/application/ports/__init__.py", "app/application/ports/conversation_repository.py"),
        ("app/application/services/__init__.py", "app/application/services/chat.py"),
        ("app/domain/__init__.py", "app/domain/policy.py"),
    ]
    codes_before = {f["code"] for f in before}
    codes_after = {f["code"] for f in after}
    assert "SHALLOW-001" not in codes_after
    assert codes_after <= codes_before - {"SHALLOW-001"}
    guarded = ("SIZE", "LAYER", "PORT", "NAMING", "AUTHN", "IMPORT", "TESTFILE")
    assert not [code for code in codes_after - codes_before if code.startswith(guarded)]
