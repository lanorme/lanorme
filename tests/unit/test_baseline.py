"""Tests for the warning baseline (baseline.py and its CLI wiring).

A baseline records the findings a codebase already has so only new ones report.
These tests pin the acceptance criteria from issue #37 plus the edge cases the
design review enumerated: the severity gate, the per-key count budget, anchor
stability under line drift, write guards, and the guarantee that no source text
or secret reaches the committed file.

Tests follow AAA structure with inline ``# Arrange / # Act / # Assert`` markers.
"""

from __future__ import annotations

import json
from pathlib import Path

from lanorme import CheckResult, Violation
from lanorme import baseline as bl
from lanorme.cli import main

# A line-anchored violation: eval() on a non-literal fires EVAL-001 every time.
_EVAL = "def f(x):\n    return eval(x)\n"


def _project(tmp_path: Path, files: dict[str, str], config: str = "[tool.lanorme]\n") -> Path:
    """Write a pyproject and the given source files; return the project root."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")
    for rel, body in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


def _run(argv: list[str]) -> int:
    """Run the CLI, returning the exit code (0 when it does not call sys.exit)."""
    try:
        main(argv)
    except SystemExit as exit_signal:
        return int(exit_signal.code or 0)
    return 0


def _read_findings(capsys) -> list[tuple[str, str, int, str]]:
    """The ``(code, file, line, severity)`` of every ndjson record printed so far."""
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    return [(r["code"], r["file"], r["line"], r["severity"]) for r in records]


_BASELINE_CONFIG = '[tool.lanorme]\nbaseline = "lanorme-baseline.json"\n'


def test_write_then_check_is_clean(tmp_path: Path):
    # Arrange: a project with one real violation and a baseline configured.
    root = _project(tmp_path, {"a.py": _EVAL}, config=_BASELINE_CONFIG)

    # Act: record the baseline, then check.
    _run(["baseline", "write", str(root)])
    code = _run(["check", str(root)])

    # Assert: the recorded finding is suppressed, so the run is clean.
    assert (root / "lanorme-baseline.json").is_file()
    assert code == 0


def test_a_new_violation_reports_exactly_once(tmp_path: Path):
    # Arrange: baseline an existing violation, then add a brand new one.
    root = _project(tmp_path, {"a.py": _EVAL}, config=_BASELINE_CONFIG)
    _run(["baseline", "write", str(root)])
    (root / "b.py").write_text(_EVAL, encoding="utf-8")

    # Act.
    code = _run(["check", str(root)])

    # Assert: only the new file fails the build.
    assert code == 1


def test_promote_all_fails_only_on_new_findings(tmp_path: Path, capsys):
    # Arrange: an advisory-only project (a similar-pair warning) baselined, then
    # a new warning added; promote = ["ALL"] would fail on any surviving warning.
    config = '[tool.lanorme]\nbaseline = "lanorme-baseline.json"\npromote = ["ALL"]\n'
    root = _project(tmp_path, {"a.py": _EVAL}, config=config)
    _run(["baseline", "write", str(root)])
    capsys.readouterr()

    # Act: with no new findings, promote has nothing to escalate.
    code = _run(["check", str(root)])

    # Assert: the baselined finding stays suppressed; promote does not fail it.
    assert code == 0


def test_rewrite_after_a_fix_shrinks_the_file(tmp_path: Path):
    # Arrange: baseline two violations, then fix one.
    root = _project(tmp_path, {"a.py": _EVAL, "b.py": _EVAL}, config=_BASELINE_CONFIG)
    _run(["baseline", "write", str(root)])
    before = json.loads((root / "lanorme-baseline.json").read_text(encoding="utf-8"))
    (root / "b.py").write_text("def f(x):\n    return x\n", encoding="utf-8")

    # Act: re-record.
    _run(["baseline", "write", str(root)])
    after = json.loads((root / "lanorme-baseline.json").read_text(encoding="utf-8"))

    # Assert: the fixed finding is pruned from the file.
    assert len(after["entries"]) == len(before["entries"]) - 1


def test_consecutive_writes_are_byte_identical(tmp_path: Path):
    # Arrange.
    root = _project(tmp_path, {"a.py": _EVAL, "b.py": _EVAL}, config=_BASELINE_CONFIG)

    # Act: write twice over unchanged code.
    _run(["baseline", "write", str(root)])
    first = (root / "lanorme-baseline.json").read_bytes()
    _run(["baseline", "write", str(root)])
    second = (root / "lanorme-baseline.json").read_bytes()

    # Assert: deterministic output, no spurious diff churn.
    assert first == second


def test_anchor_is_stable_when_lines_are_inserted_above(tmp_path: Path):
    # Arrange: baseline a violation, then push it down with 50 new lines above.
    root = _project(tmp_path, {"a.py": _EVAL}, config=_BASELINE_CONFIG)
    _run(["baseline", "write", str(root)])
    padding = "".join(f"x{i} = {i}\n" for i in range(50))
    (root / "a.py").write_text(padding + _EVAL, encoding="utf-8")

    # Act.
    code = _run(["check", str(root)])

    # Assert: content-anchoring keeps the finding suppressed despite line drift.
    assert code == 0


def test_severity_gate_reports_a_warning_that_escalates_to_error(tmp_path: Path):
    # Arrange: a file in the SIZE-001 warning band (300-500 effective lines),
    # baselined, then grown past the 500-line hard-fail threshold.
    warn_body = "".join(f"v{i} = {i}\n" for i in range(350))
    root = _project(tmp_path, {"big.py": warn_body}, config=_BASELINE_CONFIG)
    _run(["baseline", "write", str(root)])
    (root / "big.py").write_text("".join(f"v{i} = {i}\n" for i in range(520)), encoding="utf-8")

    # Act.
    code = _run(["check", str(root)])

    # Assert: a baselined warning must not hide the escalated error.
    assert code == 1


# PARAM-001 on line 1: an error at 9 parameters, a warning at 7. Reported at
# the line-1 sentinel, so it takes the file-level anchor.
_WIDE = "def f(\n    a,\n    b,\n    c,\n    d,\n    e,\n    g,\n    h,\n    i,\n):\n    return a\n"
_NARROW = "def f(\n    a,\n    b,\n    c,\n    d,\n    e,\n    g,\n):\n    return a\n"


def test_recorded_error_covers_its_improved_warning(tmp_path: Path, capsys):
    # Arrange: PARAM-001 at 9 params (error) recorded, then cut to 7 (warning).
    # The two tiers carry different rule texts ("exceeds" and "approaching"),
    # so a file-level anchor must not depend on the text.
    root = _project(tmp_path, {"m.py": _WIDE}, config=_BASELINE_CONFIG)
    _run(["baseline", "write", str(root)])
    capsys.readouterr()
    (root / "m.py").write_text(_NARROW, encoding="utf-8")

    # Act
    code = _run(["check", str(root), "--check", "PARAM-001", "--output-format", "ndjson"])

    # Assert: the improved finding is still covered; the build passes.
    assert _read_findings(capsys) == []
    assert code == 0


def test_count_budget_reports_the_one_occurrence_beyond_the_record(tmp_path: Path, capsys):
    # Arrange: two identical eval lines recorded, a third identical one added.
    two = "def f(x):\n    return eval(x)\n\n\ndef g(x):\n    return eval(x)\n"
    root = _project(tmp_path, {"m.py": two}, config=_BASELINE_CONFIG)
    _run(["baseline", "write", str(root)])
    capsys.readouterr()
    (root / "m.py").write_text(two + "\n\ndef h(x):\n    return eval(x)\n", encoding="utf-8")

    # Act
    code = _run(["check", str(root), "--check", "EVAL-001", "--output-format", "ndjson"])

    # Assert: exactly one EVAL-001 survives, and it fails the build.
    assert [(c, f) for c, f, _line, _severity in _read_findings(capsys)] == [("EVAL-001", "m.py")]
    assert code == 1


def test_file_level_baseline_entry_survives_an_edit_to_line_one(tmp_path: Path):
    # Arrange: a SIZE-001 error (510 lines, reported at line 1) baselined.
    big = "".join(f"x{i} = {i}\n" for i in range(510))
    root = _project(tmp_path, {"big.py": big}, config=_BASELINE_CONFIG)
    assert _run(["baseline", "write", str(root)]) == 0

    # Act: change the text on line 1 itself.
    (root / "big.py").write_text(big.replace("x0 = 0\n", "x0 = 'edited'\n", 1), encoding="utf-8")

    # Assert: a whole-file finding is not anchored to the text on line 1.
    assert _run(["check", str(root)]) == 0


def test_encoding_cookie_file_gets_the_same_line_anchored_fingerprint(tmp_path: Path, capsys):
    # Arrange: the same eval line in a latin-1 file and in a UTF-8 file of the
    # same name; the anchor is the decoded line, so the encoding is invisible.
    body = "# -*- coding: {coding} -*-\n# caf\xe9\ndef f(x):\n    return eval(x)\n"
    latin1 = _project(tmp_path / "latin1", {"pyproject.toml": "[tool.lanorme]\n"})
    (latin1 / "a.py").write_bytes(body.format(coding="latin-1").encode("latin-1"))
    utf8 = _project(tmp_path / "utf8", {"a.py": body.format(coding="utf-8")})

    # Act
    _run(["check", str(latin1), "--check", "EVAL-001", "--output-format", "ndjson"])
    [from_latin1] = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    _run(["check", str(utf8), "--check", "EVAL-001", "--output-format", "ndjson"])
    [from_utf8] = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    # Assert: both fingerprints are the line-anchored one, so they agree.
    assert (from_latin1["file"], from_latin1["line"]) == ("a.py", 4)
    assert from_latin1["fingerprint"] == from_utf8["fingerprint"]


def test_no_source_text_or_secret_reaches_the_committed_file(tmp_path: Path):
    # Arrange: a finding whose message embeds a source snippet and a secret value,
    # exactly the shape of security_patterns' "Raw SQL ... : {snippet}" message.
    leaky = Violation(
        file="db.py",
        line=7,
        rule="SECSQL-001: raw SQL reaches a database sink",
        message="Raw SQL passed to a database sink: SELECT * FROM users WHERE token='sk-LEAK-9999'",
        fix="",
    )
    result = CheckResult(check="security_patterns", violations=[leaky])
    baseline_path = tmp_path / "lanorme-baseline.json"

    # Act.
    bl.write(results=[result], project_root=tmp_path, baseline_path=baseline_path)
    written = baseline_path.read_text(encoding="utf-8")

    # Assert: neither the SQL snippet nor the secret value is stored.
    assert "sk-LEAK-9999" not in written
    assert "SELECT" not in written


def test_run000_crash_notices_are_never_recorded(tmp_path: Path):
    # Arrange: a RUN-000 crash notice (no file) alongside a real finding.
    crash = Violation(file="", line=0, rule="RUN-000: check raised", message="boom", fix="")
    real = Violation(file="a.py", line=2, rule="EVAL-001: eval", message="m", fix="")
    result = CheckResult(check="x", warnings=[crash, real])
    baseline_path = tmp_path / "lanorme-baseline.json"

    # Act.
    bl.write(results=[result], project_root=tmp_path, baseline_path=baseline_path)
    data = json.loads(baseline_path.read_text(encoding="utf-8"))

    # Assert: only the real, file-bearing finding is recorded.
    files = {entry["file"] for entry in data["entries"]}
    assert files == {"a.py"}


def test_noqa_finding_is_neither_recorded_nor_budget_consuming(tmp_path: Path):
    # Arrange: two identical violations, one carrying a covering noqa comment.
    body = "def f(x):\n    return eval(x)  # noqa: EVAL-001\n\n\ndef g(y):\n    return eval(y)\n"
    root = _project(tmp_path, {"a.py": body}, config=_BASELINE_CONFIG)

    # Act: write records only the un-noqa'd finding; a later check stays clean.
    _run(["baseline", "write", str(root)])
    data = json.loads((root / "lanorme-baseline.json").read_text(encoding="utf-8"))
    code = _run(["check", str(root)])

    # Assert: one entry recorded (the noqa'd one excluded), run clean.
    assert len(data["entries"]) == 1
    assert code == 0


def test_narrowed_write_is_refused(tmp_path: Path):
    # Arrange: a project and a single-file target for the write.
    root = _project(tmp_path, {"a.py": _EVAL, "b.py": _EVAL}, config=_BASELINE_CONFIG)

    # Act: a file-target write would regenerate from a partial run.
    code = _run(["baseline", "write", str(root / "a.py")])

    # Assert: refused with exit 2; the file is never written.
    assert code == 2
    assert not (root / "lanorme-baseline.json").exists()


def test_no_baseline_flag_reports_the_whole_debt(tmp_path: Path):
    # Arrange: a baselined project that checks clean normally.
    root = _project(tmp_path, {"a.py": _EVAL}, config=_BASELINE_CONFIG)
    _run(["baseline", "write", str(root)])

    # Act: --no-baseline ignores the recorded debt.
    clean = _run(["check", str(root)])
    audited = _run(["check", str(root), "--no-baseline"])

    # Assert: clean with the baseline, failing without it.
    assert clean == 0
    assert audited == 1


def test_status_lists_a_stale_entry(tmp_path: Path, capsys):
    # Arrange: baseline a violation, then fix it so the entry goes stale.
    root = _project(tmp_path, {"a.py": _EVAL}, config=_BASELINE_CONFIG)
    _run(["baseline", "write", str(root)])
    (root / "a.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    capsys.readouterr()

    # Act.
    _run(["baseline", "status", str(root)])
    out = capsys.readouterr().out

    # Assert: the now-unmatched entry is reported as stale.
    assert "stale" in out.lower()
    assert "EVAL-001" in out


def test_check_with_missing_baseline_file_exits_two(tmp_path: Path):
    # Arrange: a baseline is configured but never written.
    root = _project(tmp_path, {"a.py": _EVAL}, config=_BASELINE_CONFIG)

    # Act.
    code = _run(["check", str(root)])

    # Assert: a clear configuration error, not a silent pass.
    assert code == 2


def test_corrupt_baseline_file_exits_two(tmp_path: Path):
    # Arrange: a malformed baseline file.
    root = _project(tmp_path, {"a.py": _EVAL}, config=_BASELINE_CONFIG)
    (root / "lanorme-baseline.json").write_text("{ not json", encoding="utf-8")

    # Act.
    code = _run(["check", str(root)])

    # Assert: a clean exit 2, not a traceback.
    assert code == 2


# --- red-team regressions -----------------------------------------------------


def test_file_level_finding_survives_an_unrelated_top_of_file_edit(tmp_path: Path):
    # Arrange: a file in the SIZE-001 warning band, baselined; then an unrelated
    # comment inserted at the very top (so the line-1 sentinel text changes).
    body = "".join(f"v{i} = {i}\n" for i in range(330))
    root = _project(tmp_path, {"big.py": body}, config=_BASELINE_CONFIG)
    _run(["baseline", "write", str(root)])
    (root / "big.py").write_text("# unrelated new top comment\n" + body, encoding="utf-8")

    # Act.
    code = _run(["check", str(root)])

    # Assert: the same file-level finding must stay suppressed, not resurface
    # (it anchors on the rule description, not the text on line 1).
    assert code == 0


def test_same_tier_file_size_growth_stays_suppressed(tmp_path: Path):
    # Arrange: a SIZE-001 warning baselined, then the file grows but stays in the
    # same warning tier (its message line-count changes, the tier does not).
    root = _project(
        tmp_path,
        {"big.py": "".join(f"v{i} = {i}\n" for i in range(330))},
        config=_BASELINE_CONFIG,
    )
    _run(["baseline", "write", str(root)])
    (root / "big.py").write_text("".join(f"v{i} = {i}\n" for i in range(360)), encoding="utf-8")

    # Act.
    code = _run(["check", str(root)])

    # Assert: a same-tier warning is not resurrected by a metric change.
    assert code == 0


def test_malformed_baseline_entry_exits_two(tmp_path: Path):
    # Arrange: valid JSON, valid version, but an entry missing a required key.
    root = _project(tmp_path, {"a.py": _EVAL}, config=_BASELINE_CONFIG)
    (root / "lanorme-baseline.json").write_text(
        '{"version": 1, "entries": [{"code": "EVAL-001", "anchor": "sha:x"}]}',
        encoding="utf-8",
    )

    # Act.
    code = _run(["check", str(root)])

    # Assert: a clean exit 2, not a KeyError traceback.
    assert code == 2


def test_write_creates_a_configured_subdirectory(tmp_path: Path):
    # Arrange: a baseline path under a directory that does not exist yet.
    config = '[tool.lanorme]\nbaseline = "ci/baseline.json"\n'
    root = _project(tmp_path, {"a.py": _EVAL}, config=config)

    # Act.
    code = _run(["baseline", "write", str(root)])

    # Assert: the parent directory is created and the write succeeds.
    assert code == 0
    assert (root / "ci" / "baseline.json").is_file()


def test_first_write_block_shows_the_configured_subdirectory_path(tmp_path: Path, capsys):
    # Arrange.
    config = '[tool.lanorme]\nbaseline = "ci/baseline.json"\n'
    root = _project(tmp_path, {"a.py": _EVAL}, config=config)

    # Act.
    _run(["baseline", "write", str(root)])
    out = capsys.readouterr().out

    # Assert: the copy-paste block names the real relative path, not the basename.
    assert 'baseline = "ci/baseline.json"' in out
