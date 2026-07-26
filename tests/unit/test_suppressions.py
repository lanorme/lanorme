"""Tests for the suppressions check (SUPPRESS-001 / SUPPRESS-002, opt-in).

The check prices the escape hatches rather than closing them, so the two
properties that matter are that it counts only real directives (prose naming
``# noqa`` is documentation, not an escape) and that it cannot be waived on the
line that trips it. The last test pins that second property against the
filtering layer, because a budget an offender can suppress is not a budget.

The check is default-off; every test enables it via ``configure``. Assertions
key on the rule code, never on message text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.suppressions import SuppressionsCheck
from lanorme.filtering import _line_silences


@pytest.fixture
def check() -> SuppressionsCheck:
    """An enabled suppressions check with a zero budget."""
    instance = SuppressionsCheck()
    instance.configure(settings={"enabled": True, "max_total": 0})
    return instance


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
    _write(root=tmp_path, body="value = 1  # noqa\n")

    # Act
    result = SuppressionsCheck().run(src_root=str(tmp_path))

    # Assert
    assert result.status is Status.PASS
    assert result.violations == []


# --------------------------------------------------------------------------- #
# SUPPRESS-001: the budget
# --------------------------------------------------------------------------- #


def test_over_budget_is_flagged_once(tmp_path: Path, check: SuppressionsCheck) -> None:
    _write(root=tmp_path, body="a = 1  # noqa: TYPE-001\nb = 2  # noqa: TYPE-001\n")

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _codes(result=result) == ["SUPPRESS-001"]


def test_within_budget_passes(tmp_path: Path) -> None:
    # Arrange
    check = SuppressionsCheck()
    check.configure(settings={"enabled": True, "max_total": 2})
    _write(root=tmp_path, body="a = 1  # noqa: TYPE-001\nb = 2  # noqa: TYPE-001\n")

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_lanorme_directive_counts_too(tmp_path: Path, check: SuppressionsCheck) -> None:
    _write(root=tmp_path, body="a = 1  # lanorme: ignore[TYPE-001]\n")

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _codes(result=result) == ["SUPPRESS-001"]


def test_clean_file_passes(tmp_path: Path, check: SuppressionsCheck) -> None:
    _write(root=tmp_path, body='"""A module with nothing suppressed."""\n\nvalue = 1\n')

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


# --------------------------------------------------------------------------- #
# SUPPRESS-002: blanket directives
# --------------------------------------------------------------------------- #


def test_bare_noqa_is_blanket(tmp_path: Path, check: SuppressionsCheck) -> None:
    _write(root=tmp_path, body="a = 1  # noqa\n")

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "SUPPRESS-002" in _codes(result=result)


def test_all_code_is_blanket(tmp_path: Path, check: SuppressionsCheck) -> None:
    _write(root=tmp_path, body="a = 1  # lanorme: ignore[ALL]\n")

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "SUPPRESS-002" in _codes(result=result)


def test_targeted_directive_is_not_blanket(tmp_path: Path) -> None:
    # Arrange
    check = SuppressionsCheck()
    check.configure(settings={"enabled": True, "max_total": 5})
    _write(root=tmp_path, body="a = 1  # noqa: TYPE-001\n")

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_blanket_can_be_allowed(tmp_path: Path) -> None:
    # Arrange
    check = SuppressionsCheck()
    check.configure(settings={"enabled": True, "max_total": 5, "allow_blanket": True})
    _write(root=tmp_path, body="a = 1  # noqa\n")

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


# --------------------------------------------------------------------------- #
# Precision: prose about a directive is not a directive
# --------------------------------------------------------------------------- #


def test_prose_naming_noqa_is_not_counted(tmp_path: Path, check: SuppressionsCheck) -> None:
    # Arrange
    body = "# These paths line up with --exclude and # noqa handling.\nvalue = 1\n"
    _write(root=tmp_path, body=body)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_directive_inside_a_string_is_not_counted(tmp_path: Path, check: SuppressionsCheck) -> None:
    # Arrange
    body = 'PATTERN = "# noqa: TYPE-001"\n'
    _write(root=tmp_path, body=body)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


# --------------------------------------------------------------------------- #
# The property that makes the budget mean anything
# --------------------------------------------------------------------------- #


def test_suppress_codes_cannot_be_silenced_inline() -> None:
    # Arrange
    line = "value = 1  # noqa: SUPPRESS-001, SUPPRESS-002"

    # Act
    budget = _line_silences(line=line, rule="SUPPRESS-001: Inline suppressions must stay in budget")
    blanket = _line_silences(line=line, rule="SUPPRESS-002: A suppression must name the rule")

    # Assert
    assert budget is False
    assert blanket is False


def test_a_bare_directive_still_silences_other_rules() -> None:
    # Arrange
    line = "value = 1  # noqa"

    # Act
    silenced = _line_silences(line=line, rule="TYPE-001: Placeholder container")

    # Assert
    assert silenced is True
