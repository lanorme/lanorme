"""Tests for TYPING-001 in PatternDivergenceCheck.

Each test covers one variant of the opt-in rule:
  - enabled + encourage: flag unguarded type-only typing imports
  - enabled + encourage: do not flag already-guarded imports
  - disabled (default): no findings regardless of source
  - enabled + forbid: flag imports inside TYPE_CHECKING guard
"""

from __future__ import annotations

from lanorme.checks.pattern_divergence import PatternDivergenceCheck


def _codes(violations) -> set[str]:
    return {v.rule for v in violations}


_TYPING001_RULE = "TYPING-001: Type-only imports should use TYPE_CHECKING guard"


# ---------------------------------------------------------------------------
# TYPING-001 — encourage direction (default)
# ---------------------------------------------------------------------------


def test_typing001_fires_on_unguarded_typing_import(tmp_path):
    # Arrange
    py_file = tmp_path / "models.py"
    py_file.write_text(
        "from __future__ import annotations\n"
        "from typing import Optional\n"
        "\n"
        "def greet(name: Optional[str]) -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    check = PatternDivergenceCheck()
    check.typing_guard_enabled = True
    check.typing_guard_direction = "encourage"

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _TYPING001_RULE in _codes(result.violations)


def test_typing001_does_not_fire_when_already_guarded(tmp_path):
    # Arrange
    py_file = tmp_path / "models.py"
    py_file.write_text(
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    from typing import Optional\n"
        "\n"
        "def greet(name: Optional[str]) -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    check = PatternDivergenceCheck()
    check.typing_guard_enabled = True
    check.typing_guard_direction = "encourage"

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _TYPING001_RULE not in _codes(result.violations)


# ---------------------------------------------------------------------------
# TYPING-001 — disabled (default off)
# ---------------------------------------------------------------------------


def test_typing001_does_not_fire_when_disabled(tmp_path):
    # Arrange
    py_file = tmp_path / "models.py"
    py_file.write_text(
        "from __future__ import annotations\n"
        "from typing import Optional\n"
        "\n"
        "def greet(name: Optional[str]) -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    check = PatternDivergenceCheck()
    # typing_guard_enabled defaults to False — no configure() call needed

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _TYPING001_RULE not in _codes(result.violations)


# ---------------------------------------------------------------------------
# TYPING-001 — forbid direction
# ---------------------------------------------------------------------------


def test_typing001_forbid_fires_when_guarded(tmp_path):
    # Arrange
    py_file = tmp_path / "models.py"
    py_file.write_text(
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    from typing import Optional\n"
        "\n"
        "def greet(name: Optional[str]) -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    check = PatternDivergenceCheck()
    check.typing_guard_enabled = True
    check.typing_guard_direction = "forbid"

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _TYPING001_RULE in _codes(result.violations)
