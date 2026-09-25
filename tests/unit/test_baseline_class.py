"""The :class:`Baseline` object: one load, one line cache, one key per finding.

The CLI contract of the baseline is pinned by ``test_baseline.py`` and
``test_baseline_drift.py``; these pin the object those commands are built on:
entries merge on load, the key is memoised over a shared line cache, the file
is read once per run, and suppression honours the severity gate and budget.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lanorme import CheckResult, Violation
from lanorme import baseline as bl
from lanorme import source_lines
from lanorme.baseline import Baseline, FindingKeys
from lanorme.cli import main
from lanorme.errors import UsageError
from lanorme.source_lines import SourceLines


def _build_finding(*, file: str = "a.py", line: int = 2, code: str = "EVAL-001") -> Violation:
    return Violation(file=file, line=line, rule=f"{code}: eval", message="m", fix="f")


def _write_baseline(path: Path, entries: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    return path


def _build_entry(*, anchor: str, severity: str = "warning", count: int = 1) -> dict[str, object]:
    return {
        "file": "a.py",
        "code": "EVAL-001",
        "anchor": anchor,
        "severity": severity,
        "count": count,
    }


def _anchor_of(text: str) -> str:
    return "sha:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def keys(tmp_path: Path) -> FindingKeys:
    """Keys over a project whose ``a.py`` has two eval lines."""
    (tmp_path / "a.py").write_text("x = 1\nreturn eval(x)\nreturn eval(y)\n", encoding="utf-8")
    return FindingKeys(SourceLines(tmp_path))


def test_duplicate_entries_merge_with_the_worse_severity(tmp_path: Path, keys) -> None:
    # Arrange
    anchor = _anchor_of("return eval(x)")
    path = _write_baseline(
        tmp_path / "b.json",
        [_build_entry(anchor=anchor, count=2), _build_entry(anchor=anchor, severity="error")],
    )

    # Act
    recorded = Baseline.load(path, keys=keys)

    # Assert
    entry = recorded.entries[("a.py", "EVAL-001", anchor)]
    assert (entry.severity, entry.count) == ("error", 3)


def test_malformed_entry_is_a_usage_error(tmp_path: Path, keys) -> None:
    # Arrange
    path = _write_baseline(tmp_path / "b.json", [{"file": "a.py"}])

    # Act / Assert
    with pytest.raises(UsageError, match="malformed entry"):
        Baseline.load(path, keys=keys)


def test_key_is_memoised_over_the_first_read(tmp_path: Path, keys) -> None:
    # Arrange
    finding = _build_finding()
    first = keys.build_key(finding)

    # Act: the file changes under the run; the run keeps the key it read.
    (tmp_path / "a.py").write_text("x = 1\nsomething else\n", encoding="utf-8")
    again = keys.build_key(finding)
    fresh = FindingKeys(SourceLines(tmp_path)).build_key(finding)

    # Assert
    assert again == first == ("a.py", "EVAL-001", _anchor_of("return eval(x)"))
    assert fresh == ("a.py", "EVAL-001", _anchor_of("something else"))


def test_fingerprint_hashes_the_key_and_is_empty_without_a_file(keys) -> None:
    # Arrange
    finding = _build_finding()

    # Act
    fingerprint = keys.compute_fingerprint(finding)

    # Assert
    expected = hashlib.sha256("|".join(keys.build_key(finding)).encode("utf-8")).hexdigest()[:16]
    assert fingerprint == expected
    assert keys.compute_fingerprint(_build_finding(file="")) == ""


def test_suppress_honours_the_budget_and_the_severity_gate(tmp_path: Path, keys) -> None:
    # Arrange: one recorded warning at line 2, none at line 3.
    path = _write_baseline(tmp_path / "b.json", [_build_entry(anchor=_anchor_of("return eval(x)"))])
    recorded = Baseline.load(path, keys=keys)
    twice = [_build_finding(), _build_finding()]
    as_error = CheckResult.from_findings(check="c", violations=[_build_finding()])

    # Act
    budget = recorded.suppress([CheckResult.from_findings(check="c", warnings=twice)])
    gated = recorded.suppress([as_error])

    # Assert: the second occurrence and the error-tier one both survive.
    assert len(budget[0].warnings) == 1
    assert len(gated[0].violations) == 1


def test_stale_and_drift(tmp_path: Path, keys) -> None:
    # Arrange: the recorded anchor moved from line 2's text to line 3's.
    path = _write_baseline(tmp_path / "b.json", [_build_entry(anchor=_anchor_of("return eval(x)"))])
    recorded = Baseline.load(path, keys=keys)
    moved = [CheckResult.from_findings(check="c", warnings=[_build_finding(line=3)])]
    same = [CheckResult.from_findings(check="c", warnings=[_build_finding(line=2)])]

    # Act
    stale = recorded.find_stale(moved)
    drifted = recorded.find_drift(moved)

    # Assert
    assert stale == [("a.py", "EVAL-001", _anchor_of("return eval(x)"))]
    assert drifted == [("a.py", "EVAL-001")]
    assert recorded.find_stale(same) == []
    assert recorded.find_drift(same) == []


def test_check_reads_the_baseline_file_once(tmp_path: Path, monkeypatch) -> None:
    # Arrange
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lanorme]\nbaseline = "lanorme-baseline.json"\n',
        encoding="utf-8",
    )
    (tmp_path / "a.py").write_text("def f(x):\n    return eval(x)\n", encoding="utf-8")
    main(["baseline", "write", str(tmp_path)])
    reads: list[Path] = []
    original = bl._read_entries

    def counting(path: Path) -> list[object]:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(bl, "_read_entries", counting)

    # Act
    main(["check", str(tmp_path)])

    # Assert
    assert reads == [tmp_path / "lanorme-baseline.json"]


def test_fingerprints_reuse_the_lines_the_run_read(tmp_path: Path, monkeypatch, capsys) -> None:
    # Arrange: a finding with a baseline that does not cover it, reported as ndjson.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lanorme]\nbaseline = "lanorme-baseline.json"\n',
        encoding="utf-8",
    )
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    main(["baseline", "write", str(tmp_path)])
    (tmp_path / "a.py").write_text("def f(x):\n    return eval(x)\n", encoding="utf-8")
    decoded: list[int] = []
    original = source_lines.decode_source

    def counting(raw: bytes) -> str:
        decoded.append(len(raw))
        return original(raw)

    monkeypatch.setattr(source_lines, "decode_source", counting)
    capsys.readouterr()

    # Act
    with pytest.raises(SystemExit):
        main(["check", str(tmp_path), "--output-format=ndjson"])

    # Assert: a.py was decoded once for inline ignores, baseline and fingerprint.
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records and all(record["fingerprint"] for record in records)
    assert len(decoded) == 1
