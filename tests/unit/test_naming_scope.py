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


def _module(*, name: str, gap: int) -> str:
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


def _codes(*, result) -> list[str]:
    """The rule codes of all violations on *result*."""
    return [v.code for v in result.violations]


# --------------------------------------------------------------------------- #
# Default-off
# --------------------------------------------------------------------------- #


def test_disabled_by_default(tmp_path: Path) -> None:
    # Arrange
    _write(root=tmp_path, body=_module(name="rc", gap=40))

    # Act
    result = NamingScopeCheck().run(src_root=str(tmp_path))

    # Assert
    assert result.status is Status.PASS
    assert result.violations == []


# --------------------------------------------------------------------------- #
# The span boundary
# --------------------------------------------------------------------------- #


def test_short_name_over_a_long_span_is_flagged(tmp_path: Path, check: NamingScopeCheck) -> None:
    _write(root=tmp_path, body=_module(name="rc", gap=40))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _codes(result=result) == ["NAMING-005"]


def test_same_name_over_a_short_span_is_kept(tmp_path: Path, check: NamingScopeCheck) -> None:
    _write(root=tmp_path, body=_module(name="rc", gap=3))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_long_name_over_a_long_span_is_kept(tmp_path: Path, check: NamingScopeCheck) -> None:
    _write(root=tmp_path, body=_module(name="run_count", gap=40))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_max_span_is_configurable(tmp_path: Path) -> None:
    # Arrange
    check = NamingScopeCheck()
    check.configure(settings={"enabled": True, "max_span": 200})
    _write(root=tmp_path, body=_module(name="rc", gap=40))

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


# --------------------------------------------------------------------------- #
# Precision traps: these must never be flagged
# --------------------------------------------------------------------------- #


def test_conventional_counter_survives_any_distance(tmp_path: Path, check: NamingScopeCheck) -> None:
    _write(root=tmp_path, body=_module(name="i", gap=60))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_allowlisted_idiom_survives_any_distance(tmp_path: Path, check: NamingScopeCheck) -> None:
    _write(root=tmp_path, body=_module(name="lo", gap=60))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_allow_setting_extends_the_default(tmp_path: Path) -> None:
    # Arrange
    check = NamingScopeCheck()
    check.configure(settings={"enabled": True, "allow": ["rc"]})
    _write(root=tmp_path, body=_module(name="rc", gap=40))

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
    (tmp_path / "test_thing.py").write_text(_module(name="rc", gap=40), encoding="utf-8")

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []
