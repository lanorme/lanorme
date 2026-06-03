"""Tests for the v0.8.0 CLI output ergonomics.

These exercise the ``check`` command end-to-end through ``main(argv)`` and the
``Violation`` code field, locking the four output formats (``concise`` default,
``full``, ``json``, ``ndjson``) and the name-or-code ``--check`` selector.
"""

from __future__ import annotations

import json
from pathlib import Path

from lanorme import Violation, get_all_checks
from lanorme.cli import main

# A near-duplicate pair of 5+ statement functions (fires DRY-001) plus an
# eval() on a variable (fires EVAL-001 in security_calls).
_DIRTY = """\
def alpha(a, b):
    total = a + b
    scaled = total * 2
    shifted = scaled + 1
    result = shifted - 3
    return result


def beta(a, b):
    total = a + b
    scaled = total * 2
    shifted = scaled + 1
    result = shifted - 3
    return result


def run(expr):
    return eval(expr)
"""


def _run(argv: list[str]) -> int:
    """Invoke the CLI; return the exit code (0 when main returns normally)."""
    try:
        main(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0
    return 0


def _dirty_dir(tmp_py_file) -> Path:
    return tmp_py_file(name="m.py", body=_DIRTY).parent


def _clean_dir(tmp_py_file) -> Path:
    return tmp_py_file(name="ok.py", body="x = 1\n").parent


# --------------------------------------------------------------------------- #
# (1) concise default
# --------------------------------------------------------------------------- #


def test_concise_default_on_dirty_hides_pass_and_prints_summary(tmp_py_file, capsys):
    # Arrange: a tree with two failing checks.
    target = _dirty_dir(tmp_py_file)

    # Act: no --output-format means concise (the new default).
    code = _run(["check", str(target)])
    out = capsys.readouterr().out

    # Assert: only failing checks shown, a summary line, exit 1.
    assert code == 1
    assert "[PASS]" not in out
    assert "duplication" in out
    assert "security_calls" in out
    assert "Summary:" in out
    assert "2 failed" in out


def test_concise_default_on_clean_prints_all_passed(tmp_py_file, capsys):
    # Arrange: a tree with no findings.
    target = _clean_dir(tmp_py_file)

    # Act.
    code = _run(["check", str(target)])
    out = capsys.readouterr().out

    # Assert: the clean summary, no per-check output, exit 0.
    assert code == 0
    assert "passed." in out
    assert "Summary:" not in out
    assert "[PASS]" not in out


# --------------------------------------------------------------------------- #
# (2) full shows every registered check
# --------------------------------------------------------------------------- #


def test_full_lists_every_registered_check(tmp_py_file, capsys):
    # Arrange: a clean tree so every check is a PASS.
    target = _clean_dir(tmp_py_file)

    # Act: full restores the old list-everything behaviour.
    code = _run(["check", str(target), "--output-format", "full"])
    out = capsys.readouterr().out

    # Assert: every registered check name appears (registry is populated by main).
    assert code == 0
    assert "[PASS]" in out
    names = get_all_checks()
    assert names, "registry should be populated after a run"
    for name in names:
        assert name in out


# --------------------------------------------------------------------------- #
# (3) ndjson: one JSON object per finding, zero lines when clean
# --------------------------------------------------------------------------- #


def test_ndjson_emits_one_record_per_finding_with_all_fields(tmp_py_file, capsys):
    # Arrange.
    target = _dirty_dir(tmp_py_file)

    # Act.
    code = _run(["check", str(target), "--output-format", "ndjson"])
    out = capsys.readouterr().out

    # Assert: every line is a finding object carrying the documented fields.
    assert code == 1
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) >= 3
    expected = {"check", "severity", "code", "rule", "file", "line", "message", "fix"}
    codes: set[str] = set()
    for line in lines:
        record = json.loads(line)
        assert set(record) == expected
        assert record["severity"] in {"error", "warning"}
        codes.add(record["code"])
    assert {"DRY-001", "EVAL-001"} <= codes


def test_ndjson_is_empty_when_clean(tmp_py_file, capsys):
    # Arrange.
    target = _clean_dir(tmp_py_file)

    # Act.
    code = _run(["check", str(target), "--output-format", "ndjson"])
    out = capsys.readouterr().out

    # Assert: zero lines on a clean tree.
    assert code == 0
    assert out == ""


# --------------------------------------------------------------------------- #
# (4) json carries the new "code" field
# --------------------------------------------------------------------------- #


def test_json_findings_include_code_field(tmp_py_file, capsys):
    # Arrange.
    target = _dirty_dir(tmp_py_file)

    # Act.
    code = _run(["check", str(target), "--output-format", "json"])
    out = capsys.readouterr().out

    # Assert: each violation object carries a "code".
    assert code == 1
    payload = json.loads(out)
    violations = [v for result in payload for v in result.get("violations", [])]
    assert violations
    assert all("code" in v for v in violations)
    assert {v["code"] for v in violations} >= {"DRY-001", "EVAL-001"}


# --------------------------------------------------------------------------- #
# (5) --check by rule code runs ONLY the owning check and narrows to that code
# --------------------------------------------------------------------------- #


def test_check_by_code_runs_only_owning_check(tmp_py_file, capsys):
    # Arrange: the dirty tree also has an EVAL-001 finding in security_calls.
    target = _dirty_dir(tmp_py_file)

    # Act: --check DRY-001 should run duplication only.
    code = _run(["check", str(target), "--check", "DRY-001"])
    out = capsys.readouterr().out

    # Assert: DRY shown, the other check's finding absent.
    assert code == 1
    assert "DRY-001" in out
    assert "duplication" in out
    assert "EVAL-001" not in out
    assert "security_calls" not in out
    assert "Summary: 1 checks" in out


def test_check_by_code_is_case_insensitive(tmp_py_file, capsys):
    # Arrange.
    target = _dirty_dir(tmp_py_file)

    # Act: lowercase form must resolve identically.
    code = _run(["check", str(target), "--check", "dry-001", "--output-format", "ndjson"])
    out = capsys.readouterr().out

    # Assert: only DRY-001 records, nothing from security_calls.
    assert code == 1
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines
    codes = {json.loads(line)["code"] for line in lines}
    assert codes == {"DRY-001"}


# --------------------------------------------------------------------------- #
# (6) --check by category resolves and runs the owning check
# --------------------------------------------------------------------------- #


def test_check_by_category_resolves_and_narrows(tmp_py_file, capsys):
    # Arrange: a long function trips SIZE-002 (owned by file_limits); the
    # duplicate pair would trip DRY but must be filtered out by the selector.
    body = "def big():\n" + "".join(f"    x{i} = {i}\n" for i in range(90)) + "    return 0\n"
    tmp_py_file(name="big.py", body=body)
    target = tmp_py_file(name="m.py", body=_DIRTY).parent

    # Act: SIZE is a category selector.
    code = _run(["check", str(target), "--check", "SIZE", "--output-format", "ndjson"])
    out = capsys.readouterr().out

    # Assert: only SIZE-prefixed codes appear; DRY-001 is narrowed out.
    assert code == 1
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines
    codes = {json.loads(line)["code"] for line in lines}
    assert codes
    assert all(c.startswith("SIZE") for c in codes)
    assert "DRY-001" not in codes


# --------------------------------------------------------------------------- #
# (7) unknown selector exits 2
# --------------------------------------------------------------------------- #


def test_unknown_check_selector_exits_2(tmp_py_file, capsys):
    # Arrange.
    target = _clean_dir(tmp_py_file)

    # Act.
    code = _run(["check", str(target), "--check", "FOO-999"])
    err = capsys.readouterr().err

    # Assert: exit 2 with a helpful message on stderr.
    assert code == 2
    assert "FOO-999" in err


# --------------------------------------------------------------------------- #
# (8) Violation.code property and to_dict() include "code"
# --------------------------------------------------------------------------- #


def test_violation_code_property_and_to_dict():
    # Arrange: a violation whose rule string carries a code prefix.
    violation = Violation(
        file="x.py",
        line=1,
        rule="DRY-001: Near-duplicate function body detected",
        message="m",
        fix="f",
    )

    # Act / Assert.
    assert violation.code == "DRY-001"
    assert violation.to_dict()["code"] == "DRY-001"
