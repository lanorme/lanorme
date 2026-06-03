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


def _codes(result) -> list[str]:
    return [w.rule.split(":", 1)[0] for w in result.warnings]


def test_hasattr_literal_is_attr001_warning(tmp_path: Path):
    # Arrange + Act: flag_hasattr must be set to see ATTR-001.
    result = _run(tmp_path, "def f(x):\n    return hasattr(x, 'foo')\n", flag_hasattr=True)

    # Assert: advisory warning, not a failing violation.
    assert _codes(result) == ["ATTR-001"]
    assert result.violations == []
    assert result.status == Status.WARN


def test_getattr_two_arg_literal_is_attr002(tmp_path: Path):
    # Arrange + Act.
    result = _run(tmp_path, "def f(x):\n    return getattr(x, 'foo')\n")

    # Assert.
    assert _codes(result) == ["ATTR-002"]


def test_setattr_and_delattr_literal_are_attr002(tmp_path: Path):
    # Arrange + Act.
    result = _run(tmp_path, "def f(x):\n    setattr(x, 'foo', 1)\n    delattr(x, 'bar')\n")

    # Assert.
    assert _codes(result) == ["ATTR-002", "ATTR-002"]


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
    assert _codes(dynamic) == ["ATTR-002"]


def test_files_under_tests_are_exempt(tmp_path: Path):
    # Arrange + Act.
    result = _run(tmp_path, "def f(x):\n    return hasattr(x, 'foo')\n", name="tests/test_x.py")

    # Assert.
    assert result.warnings == []


def test_disabled_by_default(tmp_path: Path):
    # Arrange: a file that would warn if the check were on.
    path = tmp_path / "mod.py"
    path.write_text("def f(x):\n    return hasattr(x, 'foo')\n", encoding="utf-8")

    # Act: disable the check explicitly; hasattr alone does not fire without flag_hasattr.
    check = AttributeAccessCheck()
    check.configure(settings={"enabled": False})
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert result.warnings == []


def test_attr002_fires_by_default(tmp_path: Path):
    # Arrange: a file with a literal getattr (ATTR-002 target).
    root = tmp_path / "case_default"
    path = root / "mod.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def f(x):\n    return getattr(x, 'foo')\n", encoding="utf-8")

    # Act: no configure call -- enabled defaults to True.
    result = AttributeAccessCheck().run(src_root=str(root))

    # Assert: ATTR-002 fires without any explicit configuration.
    assert _codes(result) == ["ATTR-002"]
    assert result.status == Status.WARN


def test_attr001_does_not_fire_by_default(tmp_path: Path):
    # Arrange: a file with a literal hasattr (ATTR-001 target).
    root = tmp_path / "case_hasattr_default"
    path = root / "mod.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def f(x):\n    return hasattr(x, 'foo')\n", encoding="utf-8")

    # Act: no configure call -- flag_hasattr defaults to False.
    result = AttributeAccessCheck().run(src_root=str(root))

    # Assert: ATTR-001 does not fire; the check is on but hasattr is exempt by default.
    assert result.warnings == []
    assert result.status == Status.PASS


def test_attr001_fires_when_flag_hasattr_enabled(tmp_path: Path):
    # Arrange: a file with a literal hasattr.
    root = tmp_path / "case_hasattr_enabled"
    path = root / "mod.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def f(x):\n    return hasattr(x, 'foo')\n", encoding="utf-8")

    # Act: opt in to ATTR-001 via flag_hasattr.
    check = AttributeAccessCheck()
    check.configure(settings={"flag_hasattr": True})
    result = check.run(src_root=str(root))

    # Assert: ATTR-001 fires.
    assert _codes(result) == ["ATTR-001"]
    assert result.status == Status.WARN
