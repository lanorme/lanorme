"""Tests for the naming_scope check (NAMING-005, opt-in).

NAMING-005 does not ban short names, it bans carrying one past the point where
its binding is still on screen. So the suite is mostly about the boundary: the
same name passes or fails purely on the distance between binding and last use,
and the conventional-idiom allowlist has to survive any distance.

The check is default-off; every test enables it via ``configure`` and drives the
check object directly. Assertions key on the rule code, never on message text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.naming_scope import NamingScopeCheck


@pytest.fixture
def check() -> NamingScopeCheck:
    """An enabled naming_scope check (it is default-off otherwise)."""
    instance = NamingScopeCheck()
    instance.configure(settings={"enabled": True})
    return instance


def _build_module(*, name: str, gap: int) -> str:
    """A function binding *name*, then using it again *gap* lines later."""
    filler = "\n".join(f"    total += {i} - {i}" for i in range(gap))
    return (
        "def sample(rows):\n"
        '    """Walk the rows and accumulate."""\n'
        f"    {name} = 0\n"
        "    total = 0\n"
        f"{filler}\n"
        f"    return {name} + total\n"
    )


def _write(*, root: Path, body: str) -> None:
    """Write *body* as the only module under *root*."""
    (root / "sample.py").write_text(body, encoding="utf-8")


def _collect_codes(*, result) -> list[str]:
    """The rule codes of all violations on *result*."""
    return [v.code for v in result.violations]


# --------------------------------------------------------------------------- #
# Default-off
# --------------------------------------------------------------------------- #


def test_disabled_by_default(tmp_path: Path) -> None:
    # Arrange
    _write(root=tmp_path, body=_build_module(name="rc", gap=40))

    # Act
    result = NamingScopeCheck().run(src_root=str(tmp_path))

    # Assert
    assert result.status is Status.PASS
    assert result.violations == []


# --------------------------------------------------------------------------- #
# The span boundary
# --------------------------------------------------------------------------- #


def test_short_name_over_a_long_span_is_flagged(tmp_path: Path, check: NamingScopeCheck) -> None:
    _write(root=tmp_path, body=_build_module(name="rc", gap=40))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _collect_codes(result=result) == ["NAMING-005"]


def test_same_name_over_a_short_span_is_kept(tmp_path: Path, check: NamingScopeCheck) -> None:
    _write(root=tmp_path, body=_build_module(name="rc", gap=3))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_long_name_over_a_long_span_is_kept(tmp_path: Path, check: NamingScopeCheck) -> None:
    _write(root=tmp_path, body=_build_module(name="run_count", gap=40))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_max_span_is_configurable(tmp_path: Path) -> None:
    # Arrange
    check = NamingScopeCheck()
    check.configure(settings={"enabled": True, "max_span": 200})
    _write(root=tmp_path, body=_build_module(name="rc", gap=40))

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


# --------------------------------------------------------------------------- #
# Precision traps: these must never be flagged
# --------------------------------------------------------------------------- #


def test_conventional_counter_survives_any_distance(
    tmp_path: Path,
    check: NamingScopeCheck,
) -> None:
    _write(root=tmp_path, body=_build_module(name="i", gap=60))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_allowlisted_idiom_survives_any_distance(tmp_path: Path, check: NamingScopeCheck) -> None:
    _write(root=tmp_path, body=_build_module(name="lo", gap=60))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_allow_setting_extends_the_default(tmp_path: Path) -> None:
    # Arrange
    check = NamingScopeCheck()
    check.configure(settings={"enabled": True, "allow": ["rc"]})
    _write(root=tmp_path, body=_build_module(name="rc", gap=40))

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_imported_module_alias_is_not_a_local(tmp_path: Path, check: NamingScopeCheck) -> None:
    # Arrange
    filler = "\n".join(f"    total += {i} - {i}" for i in range(40))
    body = (
        "import numpy as np\n\n\n"
        "def sample(rows):\n"
        '    """The short alias is numpy\'s choice, not this function\'s."""\n'
        "    total = 0\n"
        f"{filler}\n"
        "    return np.array(rows) + total\n"
    )
    _write(root=tmp_path, body=body)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_test_files_are_skipped(tmp_path: Path, check: NamingScopeCheck) -> None:
    (tmp_path / "test_thing.py").write_text(_build_module(name="rc", gap=40), encoding="utf-8")

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


# --------------------------------------------------------------------------- #
# Skip directories are matched inside the root, never above it
# --------------------------------------------------------------------------- #


def test_root_under_a_skip_named_ancestor_is_still_scanned(
    tmp_path: Path,
    check: NamingScopeCheck,
) -> None:
    # Arrange
    root = tmp_path / "migrations" / "project"
    root.mkdir(parents=True)
    _write(root=root, body=_build_module(name="rc", gap=40))

    # Act
    result = check.run(src_root=str(root))

    # Assert
    assert _collect_codes(result=result) == ["NAMING-005"]


def test_skip_named_subdirectory_inside_the_root_is_skipped(
    tmp_path: Path,
    check: NamingScopeCheck,
) -> None:
    # Arrange
    nested = tmp_path / "migrations"
    nested.mkdir()
    _write(root=nested, body=_build_module(name="rc", gap=40))

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


# --------------------------------------------------------------------------- #
# Red-team additions: nested scopes and match captures
# --------------------------------------------------------------------------- #


def _build_filler(*, gap: int) -> str:
    """*gap* lines of harmless statements."""
    return "\n".join(f"    total += {i} - {i}" for i in range(gap))


def test_comprehension_and_lambda_names_are_their_own_scope(
    tmp_path: Path,
    check: NamingScopeCheck,
) -> None:
    # Arrange: ``r`` bound by a comprehension at the top and by a lambda at the bottom, never carried.
    body = (
        "def sample(rows):\n"
        "    keys = [r for r in rows]\n"
        "    total = 0\n"
        f"{_build_filler(gap=25)}\n"
        "    return keys, sorted(rows, key=lambda r: r.key), total\n"
    )
    _write(root=tmp_path, body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS


def test_nested_function_parameters_do_not_stretch_the_outer_extent(
    tmp_path: Path,
    check: NamingScopeCheck,
) -> None:
    # Arrange: two inner functions each take an ``s``; the outer function never binds one.
    body = (
        "def sample(items):\n"
        "    def key(s):\n"
        "        return s.lower()\n"
        "    total = 0\n"
        f"{_build_filler(gap=25)}\n"
        "    def tail(s):\n"
        "        return s[-1]\n"
        "    return sorted(items, key=key), tail, total\n"
    )
    _write(root=tmp_path, body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS


def test_outer_name_used_inside_a_nested_scope_still_counts(
    tmp_path: Path,
    check: NamingScopeCheck,
) -> None:
    # Arrange: ``rc`` bound at the top and read only inside a lambda at the bottom.
    body = (
        "def sample(rows):\n"
        "    rc = 0\n"
        "    total = 0\n"
        f"{_build_filler(gap=25)}\n"
        "    return sorted(rows, key=lambda row: row.weight + rc), total\n"
    )
    _write(root=tmp_path, body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert _collect_codes(result=result) == ["NAMING-005"]
    assert result.violations[0].line == 2


def test_match_capture_is_a_binding(tmp_path: Path, check: NamingScopeCheck) -> None:
    # Arrange: ``px`` captured by a match arm and used far below it.
    body = (
        "def sample(value):\n"
        "    match value:\n"
        "        case [px, py]:\n"
        "            total = 0\n"
        f"{_build_filler(gap=25).replace('    total', '            total')}\n"
        "            return px + py + total\n"
        "    return 0\n"
    )
    _write(root=tmp_path, body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert _collect_codes(result=result) == ["NAMING-005", "NAMING-005"]
    assert {v.line for v in result.violations} == {3}
