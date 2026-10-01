"""Tests for lanorme.line_counts: SIZE-001's effective lines and the code lines outside docstrings."""

from __future__ import annotations

from pathlib import Path

from lanorme.checks.file_limits import FileLimitsCheck
from lanorme.line_counts import count_code_lines, count_effective_lines
from lanorme.scan import Scan
from lanorme.sources import parse_module

_MIXED = (
    '"""Module docstring\n\nspans three lines."""\n'
    "\n"
    "# a comment\n"
    "import os\n"
    "\n"
    "class Thing:\n"
    '    """One line."""\n'
    "\n"
    "    def run(self):\n"
    '        """Two\n        lines."""\n'
    "        return os.sep  # trailing comment\n"
)


def read_module(tmp_path: Path, source: str):
    """Write *source* to a file and parse it."""
    path = tmp_path / "mixed.py"
    path.write_text(source, encoding="utf-8")
    with Scan(root=tmp_path).activate():
        return parse_module(path, root=tmp_path)


def test_effective_lines_agree_with_size_001(tmp_path: Path):
    # Arrange
    module = read_module(tmp_path, _MIXED)
    check = FileLimitsCheck(file_warn_lines=1)

    # Act
    counted = count_effective_lines(source=_MIXED)
    with Scan(root=tmp_path).activate() as scan:
        (warning,) = [w for w in check.check(scan).warnings if w.code == "SIZE-001"]

    # Assert: blank and comment lines are out, docstring lines are in.
    assert counted == 9
    assert f"File has {counted} effective lines" in warning.message
    assert module.relative == "mixed.py"


def test_code_lines_leave_out_module_class_and_function_docstrings(tmp_path: Path):
    # Arrange
    module = read_module(tmp_path, _MIXED)

    # Act
    counted = count_code_lines(module=module)

    # Assert: import, class, def and return.
    assert counted == 4


def test_a_docstring_only_module_has_no_code(tmp_path: Path):
    # Arrange
    module = read_module(tmp_path, '"""Only a docstring,\nover two lines."""\n')

    # Act
    counted = count_code_lines(module=module)

    # Assert
    assert counted == 0
