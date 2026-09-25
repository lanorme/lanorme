"""Tests for inline suppression: ``# noqa`` and ``# lanorme: ignore[...]``.

LaNorme reads both an on-line ``# noqa`` (shared with ruff and friends) and a
native ``# lanorme: ignore[CODE]`` directive. The native form exists because
ruff cannot parse the hyphen in our codes (``TYPE-001``): a project running both
tools silences a LaNorme finding with ``# lanorme: ignore`` and ruff never sees
an invalid directive.

Every test goes through the CLI with ndjson output, so what is asserted is the
finding a tool would still see: a directive silences the line and code it
names and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lanorme.cli import main

# Two eval() calls; the directive under test goes on the first (line 2).
_TWO_EVALS = "def f(x, y):\n    a = eval(x)  {directive}\n    return eval(y)\n"
# A five-parameter def, the PARAM-001 warning, on line 3.
_WIDE_DEF = "\n\ndef f(a, b, c, d, e):  {directive}\n    return a\n"


def _run(argv: list[str]) -> int:
    try:
        main(argv)
    except SystemExit as exit_signal:
        return int(exit_signal.code or 0)
    return 0


def _read_findings(capsys) -> list[tuple[str, str, int, str]]:
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    return [(r["code"], r["file"], r["line"], r["severity"]) for r in records]


def _write_project(root: Path, body: str | bytes) -> None:
    (root / "pyproject.toml").write_text("[tool.lanorme]\n", encoding="utf-8")
    if isinstance(body, bytes):
        (root / "m.py").write_bytes(body)
    else:
        (root / "m.py").write_text(body, encoding="utf-8")


@pytest.mark.parametrize(
    ("directive", "silenced"),
    [
        ("# lanorme: ignore[EVAL-001]", True),
        ("# lanorme: ignore[EVAL]", True),
        ("# lanorme: ignore[SQL-001, EVAL-001]", True),
        ("# LaNorme: Ignore[eval-001]", True),
        ("# lanorme: ignore", True),
        ("# noqa: EVAL-001", True),
        ("# noqa", True),
        ("# lanorme: ignore[SQL-001]", False),
        ("# noqa: SQL-001", False),
        ("# a plain comment", False),
    ],
)
def test_inline_directive_silences_only_the_line_and_code_it_names(
    tmp_path: Path,
    capsys,
    directive: str,
    silenced: bool,
):
    # Arrange
    _write_project(tmp_path, _TWO_EVALS.format(directive=directive))

    # Act
    code = _run(["check", str(tmp_path), "--check", "EVAL-001", "--output-format", "ndjson"])

    # Assert: line 3 always reports; line 2 only when the directive misses it.
    expected = [("EVAL-001", "m.py", 3, "error")]
    if not silenced:
        expected.insert(0, ("EVAL-001", "m.py", 2, "error"))
    assert _read_findings(capsys) == expected
    assert code == 1


def test_native_directive_silences_a_warning_too(tmp_path: Path, capsys):
    # Arrange: the sole finding is an advisory warning on the directive's line.
    _write_project(tmp_path, _WIDE_DEF.format(directive="# lanorme: ignore[PARAM-001]"))

    # Act
    code = _run(["check", str(tmp_path), "--check", "PARAM-001", "--output-format", "ndjson"])

    # Assert: nothing reports and the run is clean.
    assert _read_findings(capsys) == []
    assert code == 0


def test_directive_in_an_encoding_cookie_file_is_honoured(tmp_path: Path, capsys):
    # Arrange: a latin-1 file (undecodable as UTF-8) with a covering noqa on
    # line 4 and a second, uncovered eval on line 8.
    latin1 = (
        "# -*- coding: latin-1 -*-\n# caf\xe9\ndef f(x):\n    return eval(x)  # noqa: EVAL-001\n\n\n"
        "def g(y):\n    return eval(y)\n"
    ).encode("latin-1")
    _write_project(tmp_path, latin1)

    # Act
    code = _run(["check", str(tmp_path), "--check", "EVAL-001", "--output-format", "ndjson"])

    # Assert: the directive is read through the cookie, so only line 8 reports.
    assert _read_findings(capsys) == [("EVAL-001", "m.py", 8, "error")]
    assert code == 1
