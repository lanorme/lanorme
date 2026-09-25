"""Tests for the attribute_access check (ATTR-001 / ATTR-002)."""

from __future__ import annotations

from pathlib import Path

from lanorme import Status
from lanorme.checks.attribute_access import AttributeAccessCheck


def _run(tmp_path: Path, body: str, *, name: str = "mod.py", **cfg: object):
    # Each call gets its own root so repeated calls in one test do not see
    # each other's files.
    root = tmp_path / f"case_{name.replace('/', '_')}"
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    check = AttributeAccessCheck()
    check.configure(settings={"enabled": True, **cfg})
    return check.run(src_root=str(root))


def _collect_codes(result) -> list[str]:
    return [w.rule.split(":", 1)[0] for w in result.warnings]


def test_hasattr_literal_is_attr001_warning(tmp_path: Path):
    # Arrange + Act.
    result = _run(tmp_path, "def f(x):\n    return hasattr(x, 'foo')\n")

    # Assert: advisory warning, not a failing violation.
    assert _collect_codes(result) == ["ATTR-001"]
    assert result.violations == []
    assert result.status == Status.WARN


def test_getattr_two_arg_literal_is_attr002(tmp_path: Path):
    # Arrange + Act.
    result = _run(tmp_path, "def f(x):\n    return getattr(x, 'foo')\n")

    # Assert.
    assert _collect_codes(result) == ["ATTR-002"]


def test_setattr_and_delattr_literal_are_attr002(tmp_path: Path):
    # Arrange + Act.
    result = _run(tmp_path, "def f(x):\n    setattr(x, 'foo', 1)\n    delattr(x, 'bar')\n")

    # Assert.
    assert _collect_codes(result) == ["ATTR-002", "ATTR-002"]


def test_three_arg_getattr_is_exempt(tmp_path: Path):
    # Arrange + Act: the safe-access idiom with a default.
    result = _run(tmp_path, "def f(x):\n    return getattr(x, 'foo', None)\n")

    # Assert.
    assert result.status == Status.PASS
    assert result.warnings == []


def test_dunder_and_non_identifier_names_are_exempt(tmp_path: Path):
    # Arrange + Act.
    result = _run(
        tmp_path,
        "def f(x):\n    a = hasattr(x, '__iter__')\n    b = getattr(x, 'weird-key')\n    return a, b\n",
    )

    # Assert.
    assert result.warnings == []


def test_dynamic_name_exempt_by_default_flagged_when_enabled(tmp_path: Path):
    # Arrange.
    body = "def f(x, name):\n    return getattr(x, name)\n"

    # Act: default leaves reflection alone; flag_dynamic opts into it.
    default = _run(tmp_path, body, name="a.py")
    dynamic = _run(tmp_path, body, name="b.py", flag_dynamic=True)

    # Assert.
    assert default.warnings == []
    assert _collect_codes(dynamic) == ["ATTR-002"]


def test_files_under_tests_are_exempt(tmp_path: Path):
    # Arrange + Act.
    result = _run(tmp_path, "def f(x):\n    return hasattr(x, 'foo')\n", name="tests/test_x.py")

    # Assert.
    assert result.warnings == []


def test_disabled_by_default(tmp_path: Path):
    # Arrange: a file that would warn if the check were on.
    path = tmp_path / "mod.py"
    path.write_text("def f(x):\n    return hasattr(x, 'foo')\n", encoding="utf-8")

    # Act: the default check (no configure) ships off.
    result = AttributeAccessCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert result.warnings == []


def test_probing_an_imported_module_is_feature_detection(tmp_path: Path):
    # Arrange: hasattr / getattr on names a plain `import` binds to a module
    # (platform feature detection), beside the same probes on an object and on
    # a `from` import, which may bind anything.
    result = _run(
        tmp_path,
        "import os\n"
        "import sys as system\n"
        "from os import path\n\n"
        "HAS_FORK = hasattr(os, 'fork')\n"
        "VERSION = getattr(system, 'getwindowsversion')\n\n"
        "def probe(obj):\n"
        "    return hasattr(obj, 'fork'), hasattr(path, 'fork')\n",
    )

    # Act + Assert: only the object and the from-import receivers warn.
    assert [(w.rule.split(":", 1)[0], w.line) for w in result.warnings] == [
        ("ATTR-001", 9),
        ("ATTR-001", 9),
    ]


def test_writing_to_an_imported_module_is_not_feature_detection(tmp_path: Path):
    # Arrange + Act: ``settings`` is a module, but setting and deleting on it is mutation.
    result = _run(
        tmp_path,
        "import settings\n\nsetattr(settings, 'DEBUG', True)\ndelattr(settings, 'CACHE')\n",
    )

    # Assert.
    assert [(w.code, w.file, w.line) for w in result.warnings] == [
        ("ATTR-002", "mod.py", 3),
        ("ATTR-002", "mod.py", 4),
    ]


def test_dispatch_through_an_imported_module_is_dynamic_access(tmp_path: Path):
    # Arrange + Act: a computed name on a module is reflection, not detection.
    result = _run(
        tmp_path,
        "import handlers\n\n\ndef dispatch(action):\n    return getattr(handlers, action)()\n",
        flag_dynamic=True,
    )

    # Assert.
    assert [(w.code, w.file, w.line) for w in result.warnings] == [("ATTR-002", "mod.py", 5)]
