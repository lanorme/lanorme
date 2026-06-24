"""Tests for inline suppression: ``# noqa`` and ``# lanorme: ignore[...]``.

LaNorme reads both an on-line ``# noqa`` (shared with ruff and friends) and a
native ``# lanorme: ignore[CODE]`` directive. The native form exists because
ruff cannot parse the hyphen in our codes (``TYPE-001``): a project running both
tools silences a LaNorme finding with ``# lanorme: ignore`` and ruff never sees
an invalid directive. See ``filtering._line_silences``.
"""

from __future__ import annotations

from pathlib import Path

from lanorme import CheckResult, Status, Violation
from lanorme.filtering import _apply_inline_ignores, _line_silences


def test_native_directive_silences_matching_code():
    assert _line_silences(line="x = 1  # lanorme: ignore[TYPE-001]", rule="TYPE-001: detail")


def test_native_directive_ignores_other_code():
    assert not _line_silences(line="x = 1  # lanorme: ignore[TYPE-001]", rule="PARAM-001: detail")


def test_native_directive_bare_silences_any_rule():
    assert _line_silences(line="x = 1  # lanorme: ignore", rule="PARAM-001: detail")


def test_native_directive_accepts_code_list():
    # Arrange.
    line = "x = 1  # lanorme: ignore[TYPE-001, PARAM-001]"

    # Act / Assert.
    assert _line_silences(line=line, rule="TYPE-001: d")
    assert _line_silences(line=line, rule="PARAM-001: d")
    assert not _line_silences(line=line, rule="SQL-001: d")


def test_native_directive_matches_category():
    assert _line_silences(line="x = 1  # lanorme: ignore[TYPE]", rule="TYPE-004: d")


def test_native_directive_is_case_insensitive():
    assert _line_silences(line="x = 1  # LaNorme: Ignore[type-001]", rule="TYPE-001: d")


def test_noqa_still_silences():
    # Regression: the existing shared ``# noqa`` path is untouched.
    assert _line_silences(line="x = 1  # noqa: TYPE-001", rule="TYPE-001: d")
    assert _line_silences(line="x = 1  # noqa", rule="ANY-001: d")
    assert not _line_silences(line="x = 1  # noqa: SQL-001", rule="TYPE-001: d")


def test_no_directive_does_not_silence():
    assert not _line_silences(line="x = 1  # a plain comment", rule="TYPE-001: d")


def test_native_directive_is_invisible_to_ruff_grammar():
    # The whole point of the native form: it carries no ``noqa`` token for ruff
    # to misparse, yet still silences LaNorme.
    line = "x = eval(y)  # lanorme: ignore[EVAL-001]"
    assert "noqa" not in line
    assert _line_silences(line=line, rule="EVAL-001: avoid eval")


def _violation(*, file: str, line: int, code: str) -> Violation:
    return Violation(file=file, line=line, rule=f"{code}: detail", message="m", fix="f")


def test_apply_drops_native_ignored_finding_and_clears_status(tmp_path: Path):
    # Arrange: two findings, one carrying a covering native directive.
    src = tmp_path / "m.py"
    src.write_text(
        "a = eval(x)  # lanorme: ignore[EVAL-001]\nb = eval(y)\n", encoding="utf-8"
    )
    result = CheckResult(
        check="c",
        status=Status.FAIL,
        violations=[
            _violation(file="m.py", line=1, code="EVAL-001"),
            _violation(file="m.py", line=2, code="EVAL-001"),
        ],
        warnings=[],
    )

    # Act.
    filtered = _apply_inline_ignores(results=[result], project_root=tmp_path)

    # Assert: only the un-suppressed finding survives; status stays FAIL.
    assert [v.line for v in filtered[0].violations] == [2]
    assert filtered[0].status == Status.FAIL


def test_apply_native_ignore_flips_status_to_pass(tmp_path: Path):
    # Arrange: the sole finding is silenced by the native directive.
    src = tmp_path / "m.py"
    src.write_text("a = eval(x)  # lanorme: ignore\n", encoding="utf-8")
    result = CheckResult(
        check="c",
        status=Status.FAIL,
        violations=[_violation(file="m.py", line=1, code="EVAL-001")],
        warnings=[],
    )

    # Act.
    filtered = _apply_inline_ignores(results=[result], project_root=tmp_path)

    # Assert.
    assert not filtered[0].violations
    assert filtered[0].status == Status.PASS


def test_apply_native_ignore_silences_warning(tmp_path: Path):
    # Arrange: advisory warning carrying a covering native directive.
    src = tmp_path / "m.py"
    src.write_text("a = b  # lanorme: ignore[TYPE-004]\n", encoding="utf-8")
    result = CheckResult(
        check="c",
        status=Status.WARN,
        violations=[],
        warnings=[_violation(file="m.py", line=1, code="TYPE-004")],
    )

    # Act.
    filtered = _apply_inline_ignores(results=[result], project_root=tmp_path)

    # Assert.
    assert not filtered[0].warnings
    assert filtered[0].status == Status.PASS
