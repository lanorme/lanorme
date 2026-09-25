"""Contract pins for the run pipeline that a tautology audit found unguarded.

Each test here killed a mutant that survived the whole suite. They go through
``main([...])`` with ndjson output, or through ``lanorme.run_check``, and assert
the exact record a tool would read: the code, file, line, scope, severity and
fingerprint of a known finding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import lanorme
from lanorme import CheckResult, Violation
from lanorme.cli import main
from lanorme.scan import Scan


def _run(argv: list[str]) -> int:
    try:
        main(argv)
    except SystemExit as exit_signal:
        return int(exit_signal.code or 0)
    return 0


def _read_ndjson(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def _write_project(root: Path, **files: str) -> None:
    (root / "pyproject.toml").write_text("[tool.lanorme]\n", encoding="utf-8")
    for name, body in files.items():
        (root / f"{name}.py").write_text(body, encoding="utf-8")


def test_bare_code_on_a_warning_is_expanded(tmp_path: Path):
    # Arrange: a check that emits the bare code on a warning, not a violation.
    @dataclass
    class _Terse:
        name: str = "terse"
        description: str = "d"
        rules: list[str] = field(default_factory=lambda: ["TERSE-001: Say it once"])

        def check(self, scan: Scan) -> CheckResult:
            w = Violation(file="f.py", line=3, rule="TERSE-001", message="m", fix="x")
            return CheckResult.from_findings(check=self.name, warnings=[w])

    # Act
    result = lanorme.run_check(_Terse(), src_root=str(tmp_path))

    # Assert: warnings are expanded to the declared string like violations are.
    assert [w.rule for w in result.warnings] == ["TERSE-001: Say it once"]


def test_two_codes_on_one_line_have_distinct_fingerprints(tmp_path: Path, capsys):
    # Arrange: one def that is both too wide (PARAM-001) and too long (SIZE-002).
    params = ", ".join(f"p{i}" for i in range(9))
    body = "\n".join(f"    v{i} = {i}" for i in range(85))
    _write_project(tmp_path, m=f"\ndef f({params}):\n{body}\n")

    # Act
    _run(["check", str(tmp_path), "--output-format", "ndjson"])
    records = {r["code"]: r for r in _read_ndjson(capsys) if r["file"] == "m.py" and r["line"] == 2}

    # Assert: the code is part of the identity, not just the file and line.
    assert {"PARAM-001", "SIZE-002"} <= set(records)
    assert records["PARAM-001"]["fingerprint"] != records["SIZE-002"]["fingerprint"]


def test_bool_for_an_integer_threshold_is_a_config_error(tmp_path: Path, capsys):
    # Arrange: ``true`` where an integer is expected (a bool is an int in Python).
    (tmp_path / "lanorme.toml").write_text("[file_limits]\nparam_warn = true\n", encoding="utf-8")
    (tmp_path / "sample.py").write_text("x = 1\n", encoding="utf-8")

    # Act
    code = _run(["check", str(tmp_path)])

    # Assert: refused as a config error naming the key, not coerced to 1.
    assert code == 2
    assert "[tool.lanorme.file_limits] param_warn" in capsys.readouterr().err


def test_whole_file_finding_has_file_scope_and_def_finding_has_span(tmp_path: Path, capsys):
    # Arrange: a 310-line file (SIZE-001 at the line-1 sentinel) ending in a
    # five-parameter def (PARAM-001 at its own line).
    lines = "".join(f"x{i} = {i}\n" for i in range(310))
    _write_project(tmp_path, m=lines + "def f(a, b, c, d, e):\n    return a\n")

    # Act
    _run(["check", str(tmp_path), "--output-format", "ndjson"])
    by_code = {r["code"]: r for r in _read_ndjson(capsys) if r["file"] == "m.py"}

    # Assert: line 1 with no position is the whole file; a def is a span.
    assert (by_code["SIZE-001"]["line"], by_code["SIZE-001"]["scope"]) == (1, "file")
    assert (by_code["PARAM-001"]["line"], by_code["PARAM-001"]["scope"]) == (311, "span")
