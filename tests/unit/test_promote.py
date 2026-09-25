"""Tests for promoting advisory warnings to build-failing errors.

A heuristic rule (TYPE-004, SIMILAR-001, ...) ships as a default-on warning so
it never breaks a build unasked. A project that wants it enforced lists its
code or category under ``[tool.lanorme] promote`` (or passes ``--promote``),
which moves the matching warnings into violations and flips the exit code.

Every test goes through the CLI: the severity a tool reads from the ndjson
record and the exit code are the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lanorme.cli import main

# One PARAM-001 warning (5 params) on line 3.
_WARNING_ONLY = "\n\ndef f(a, b, c, d, e):\n    return a\n"
# The same warning plus an EVAL-001 error on line 4.
_WARNING_AND_ERROR = "\n\ndef f(a, b, c, d, e):\n    return eval(a)\n"
_POSITIVE = "def get_user(id: int):\n    return db.get(id)\n"


def _run(argv: list[str]) -> int:
    try:
        main(argv)
    except SystemExit as exit_signal:
        return int(exit_signal.code or 0)
    return 0


def _read_records(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def _read_findings(capsys) -> list[tuple[str, str, int, str]]:
    return [(r["code"], r["file"], r["line"], r["severity"]) for r in _read_records(capsys)]


def _write_project(root: Path, *, config: str = "[tool.lanorme]\n", **files: str) -> None:
    (root / "pyproject.toml").write_text(config, encoding="utf-8")
    for name, body in files.items():
        (root / f"{name}.py").write_text(body, encoding="utf-8")


@pytest.mark.parametrize(
    ("promote", "severity", "exit_code"),
    [
        ("PARAM-001", "error", 1),
        ("param-001", "error", 1),
        ("PARAM", "error", 1),
        ("ALL", "error", 1),
        ("DRY-001", "warning", 0),
    ],
)
def test_promote_flag_escalates_matching_warnings_only(
    tmp_path: Path,
    capsys,
    promote: str,
    severity: str,
    exit_code: int,
):
    # Arrange
    _write_project(tmp_path, m=_WARNING_ONLY)

    # Act
    code = _run(
        [
            "check",
            str(tmp_path),
            "--check",
            "PARAM-001",
            "--promote",
            promote,
            "--output-format",
            "ndjson",
        ],
    )

    # Assert
    assert _read_findings(capsys) == [("PARAM-001", "m.py", 3, severity)]
    assert code == exit_code


def test_promote_in_config_is_normalised_like_the_flag(tmp_path: Path, capsys):
    # Arrange: stray case and whitespace in a config selector.
    _write_project(tmp_path, config='[tool.lanorme]\npromote = [" param-001 "]\n', m=_WARNING_ONLY)

    # Act
    code = _run(["check", str(tmp_path), "--check", "PARAM-001", "--output-format", "ndjson"])

    # Assert
    assert _read_findings(capsys) == [("PARAM-001", "m.py", 3, "error")]
    assert code == 1


def test_promote_accepts_a_bare_string_in_config(tmp_path: Path, capsys):
    # Arrange: ``promote = "ALL"`` must not iterate into ['A', 'L', 'L'].
    _write_project(tmp_path, config='[tool.lanorme]\npromote = "ALL"\n', m=_WARNING_ONLY)

    # Act
    code = _run(["check", str(tmp_path), "--check", "PARAM-001", "--output-format", "ndjson"])

    # Assert
    assert _read_findings(capsys) == [("PARAM-001", "m.py", 3, "error")]
    assert code == 1


def test_non_list_promote_in_config_promotes_nothing(tmp_path: Path, capsys):
    # Arrange: a value that is neither a list nor a string is ignored, not iterated.
    _write_project(tmp_path, config="[tool.lanorme]\npromote = 5\n", m=_WARNING_ONLY)

    # Act
    code = _run(["check", str(tmp_path), "--check", "PARAM-001", "--output-format", "ndjson"])

    # Assert
    assert _read_findings(capsys) == [("PARAM-001", "m.py", 3, "warning")]
    assert code == 0


def test_promoted_warning_joins_existing_violations(tmp_path: Path, capsys):
    # Arrange: a real error already present alongside the warning.
    _write_project(tmp_path, m=_WARNING_AND_ERROR)

    # Act
    code = _run(
        [
            "check",
            str(tmp_path),
            "--select",
            "PARAM-001,EVAL-001",
            "--promote",
            "PARAM-001",
            "--output-format",
            "ndjson",
        ],
    )

    # Assert: both report as errors.
    assert set(_read_findings(capsys)) == {
        ("PARAM-001", "m.py", 3, "error"),
        ("EVAL-001", "m.py", 4, "error"),
    }
    assert code == 1


def test_promoted_warning_is_marked_promoted_in_read_ndjson(tmp_path: Path, capsys):
    # Arrange
    _write_project(tmp_path, m=_WARNING_ONLY)

    # Act
    code = _run(["check", str(tmp_path), "--promote", "PARAM-001", "--output-format", "ndjson"])
    (record,) = [r for r in _read_records(capsys) if r["code"] == "PARAM-001"]

    # Assert: the record says it was promoted, not born an error.
    assert code == 1
    assert (record["file"], record["line"], record["severity"]) == ("m.py", 3, "error")
    assert record["promoted"] is True


def test_skip_notice_is_not_promoted_even_by_all(tmp_path: Path, capsys):
    # Arrange: a file that does not parse yields ``-000`` notices, not findings.
    _write_project(tmp_path, bad="def (:\n")

    # Act
    code = _run(["check", str(tmp_path), "--promote", "ALL", "--output-format", "ndjson"])
    records = _read_records(capsys)

    # Assert: every record is a warning-tier notice and the build passes.
    assert records
    assert {r["code"][-4:] for r in records} == {"-000"}
    assert {(r["severity"], r["promoted"]) for r in records} == {("warning", False)}
    assert code == 0


def test_default_warning_does_not_fail_the_build(tmp_path: Path, capsys):
    # Arrange
    (tmp_path / "svc.py").write_text(_POSITIVE, encoding="utf-8")

    # Act: a TYPE-004 warning alone must not raise SystemExit.
    main(["check", str(tmp_path), "--check", "strong_types", "--json"])

    # Assert: it surfaced as a warning, not an error.
    out = capsys.readouterr().out
    assert "TYPE-004" in out


def test_config_promote_makes_the_build_fail(tmp_path: Path, capsys):
    # Arrange: the project promotes TYPE-004 via pyproject.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lanorme]\npromote = ["TYPE-004"]\n',
        encoding="utf-8",
    )
    (tmp_path / "svc.py").write_text(_POSITIVE, encoding="utf-8")

    # Act / Assert
    with pytest.raises(SystemExit) as exc:
        main(["check", str(tmp_path), "--check", "strong_types", "--json"])
    assert exc.value.code == 1


def test_cli_promote_flag_makes_the_build_fail(tmp_path: Path, capsys):
    # Arrange
    (tmp_path / "svc.py").write_text(_POSITIVE, encoding="utf-8")

    # Act / Assert
    with pytest.raises(SystemExit) as exc:
        main(["check", str(tmp_path), "--check", "strong_types", "--promote", "TYPE-004", "--json"])
    assert exc.value.code == 1


def test_ignored_warning_is_not_promoted(tmp_path: Path, capsys):
    # Arrange: the same code is both ignored and promoted; ignore drops it first.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lanorme]\nignore = ["TYPE-004"]\npromote = ["TYPE-004"]\n',
        encoding="utf-8",
    )
    (tmp_path / "svc.py").write_text(_POSITIVE, encoding="utf-8")

    # Act: no SystemExit means the build passed (the warning was filtered, not promoted).
    main(["check", str(tmp_path), "--check", "strong_types", "--json"])


def test_show_config_surfaces_promote(tmp_path: Path, capsys):
    # Arrange
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lanorme]\npromote = ["TYPE-004"]\n',
        encoding="utf-8",
    )
    (tmp_path / "svc.py").write_text(_POSITIVE, encoding="utf-8")

    # Act
    main(["check", str(tmp_path), "--show-config"])

    # Assert: the build-failing setting is visible in the dump.
    assert "promote" in capsys.readouterr().out
