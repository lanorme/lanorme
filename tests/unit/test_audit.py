"""The evaluation harness must be honest: split, complete, reproducible and gated.

``evals/audit.py`` is the release audit trail: it validates every labelled
corpus, scores each rule on its dev and holdout splits, and stamps the run with
version and hardware metadata. These tests run it end to end in accuracy-only
mode (``--no-perf``) against a throwaway output path, so no
``evals/results/v*.json`` is left behind, and pin the pieces that keep the
numbers honest: the hash split is stable, every corpus file and comment is
labelled, the generated cases are reproducible, and the holdout gate fails on a
drop beyond its tolerance and only then.

Tests follow AAA structure with inline ``# Arrange / # Act / # Assert`` markers.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

import generate_adversarial  # noqa: E402 -- the evals directory is not a package
import labelled_corpus  # noqa: E402
import regression_gate  # noqa: E402
import validate_corpora  # noqa: E402

_AUDIT = _EVALS / "audit.py"

_METADATA_KEYS = {
    "lanorme_version",
    "git_commit",
    "git_dirty",
    "python_version",
    "platform",
    "processor",
    "timestamp_utc",
}
_KNOWN_RULES = {"CMT-001", "CMT-005", "SQL-001", "SECRETPY-001", "SIMILAR-001", "DRY-001"}


def run_audit(*, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the audit harness with *args*, inheriting the test environment."""
    return subprocess.run(
        [sys.executable, str(_AUDIT), *args],
        capture_output=True,
        text=True,
    )


def write_audit(*, path: Path, accuracy: list[dict]) -> Path:
    """Write a minimal previous-audit JSON holding *accuracy*."""
    path.write_text(json.dumps({"accuracy": accuracy}), encoding="utf-8")
    return path


def build_record(*, rule: str, dev_recall: float, holdout_recall: float | None) -> dict:
    """Return an accuracy record with the given dev and holdout recall."""
    holdout = None
    if holdout_recall is not None:
        holdout = {"precision": 1.0, "recall": holdout_recall}
    return {"rule": rule, "dev": {"precision": 1.0, "recall": dev_recall}, "holdout": holdout}


def build_comment_corpus(*, root: Path, labelled_lines: list[int]) -> Path:
    """Create a one-file comment corpus under *root* labelling *labelled_lines*."""
    corpus = root / "tiny_comments"
    (corpus / "dev" / "positives").mkdir(parents=True)
    (corpus / "dev" / "positives" / "pos_a.py").write_text(
        "# x = 1\nvalue = 2\n# y = 3\n",
        encoding="utf-8",
    )
    document = {
        "rules": ["CMT-001"],
        "unit": "comment",
        "description": "test corpus",
        "split": "too_small",
        "files": {
            "dev/positives/pos_a.py": {
                "source": "hand-written",
                "labelled_by": "test",
                "labelled_before_rule": True,
                "labels": [{"line": line, "flag": True} for line in labelled_lines],
            },
        },
    }
    (corpus / "labels.json").write_text(json.dumps(document), encoding="utf-8")
    return corpus


def test_audit_writes_metadata_and_per_split_accuracy(tmp_path):
    # Arrange: a throwaway output path so no results/v*.json is committed.
    out = tmp_path / "audit.json"

    # Act: run the harness in accuracy-only mode.
    result = run_audit(args=["--no-perf", "--version", "0.0.0-test", "--output", str(out)])

    # Assert: clean exit, the metadata stamp, no corpus problem.
    assert result.returncode == 0, result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert _METADATA_KEYS <= set(report["metadata"])
    assert report["metadata"]["audited_version"] == "0.0.0-test"
    assert report["corpus_problems"] == []
    assert report["gate"] is None
    # Assert: every known rule is scored, with combined, dev and holdout blocks.
    assert _KNOWN_RULES <= {entry.get("rule") for entry in report["accuracy"]}
    for entry in report["accuracy"]:
        assert "error" not in entry, entry
        assert {"precision", "recall", "f1", "dev", "holdout", "gap"} <= set(entry)
        assert (entry["holdout"] is None) == (entry["split"] == "too_small")


def test_audit_rejects_missing_version(tmp_path):
    # Arrange: invoke without --version.
    out = tmp_path / "audit.json"

    # Act.
    result = run_audit(args=["--no-perf", "--output", str(out)])

    # Assert: usage error exit 2 and no file written.
    assert result.returncode == 2
    assert not out.exists()


def test_audit_gate_fails_on_a_holdout_regression(tmp_path):
    # Arrange: a baseline whose SIMILAR-001 holdout precision beats today's by 0.05.
    out = tmp_path / "audit.json"
    first = run_audit(args=["--no-perf", "--version", "0.0.0-test", "--output", str(out)])
    assert first.returncode == 0, first.stderr
    accuracy = json.loads(out.read_text(encoding="utf-8"))["accuracy"]
    similar = next(entry for entry in accuracy if entry["rule"] == "SIMILAR-001")
    similar["holdout"]["precision"] += 0.05
    baseline = write_audit(path=tmp_path / "previous.json", accuracy=accuracy)

    # Act: gate today's run against the inflated baseline.
    result = run_audit(
        args=[
            "--no-perf",
            "--version",
            "0.0.0-test",
            "--output",
            str(out),
            "--gate",
            str(baseline),
        ],
    )

    # Assert: exit 1, and the regression names the rule.
    assert result.returncode == 1
    assert "gate: REGRESSION SIMILAR-001: holdout precision" in result.stdout


def test_split_assignment_is_pinned():
    # Arrange: inner paths whose split was recorded when the corpora were split.
    pinned = {
        "positives/pos_x.py": "dev",
        "fire_api_overview.md": "holdout",
        "positives/recall-gaps_01_reorder_independent.py": "dev",
        "negatives/alembic/versions/0001_initial.py": "holdout",
    }

    # Act.
    assigned = {name: labelled_corpus.assign_split(name=name) for name in pinned}

    # Assert: the hash rule has not changed, so no file would move.
    assert assigned == pinned


def test_every_corpus_file_sits_in_its_hashed_split():
    # Arrange: every hand-labelled file of every corpus, with its corpus' split.
    placed: list[tuple[str, str, str, str]] = []
    for corpus in sorted(p for p in labelled_corpus.CORPORA_ROOT.iterdir() if p.is_dir()):
        document = labelled_corpus.read_labels(corpus=corpus)
        for path, entry in document["files"].items():
            if not entry["source"].startswith("generated:"):
                side, _, inner = path.partition("/")
                placed.append((f"{corpus.name}/{path}", side, inner, document["split"]))

    # Act: recompute where each file's name sends it (a too-small corpus is all dev).
    misplaced = [
        where
        for where, side, inner, split in placed
        if side != ("dev" if split == "too_small" else labelled_corpus.assign_split(name=inner))
    ]

    # Assert.
    assert len(placed) > 100
    assert misplaced == []


def test_committed_corpora_validate_clean():
    # Arrange / Act.
    problems = validate_corpora.find_problems()

    # Assert.
    assert problems == []


def test_validator_flags_an_unlabelled_comment(tmp_path):
    # Arrange: two comments, only the first labelled.
    corpus = build_comment_corpus(root=tmp_path, labelled_lines=[1])

    # Act.
    problems = validate_corpora.find_corpus_problems(corpus=corpus)

    # Assert.
    assert problems == ["tiny_comments: dev/positives/pos_a.py:3: comment has no label"]


def test_validator_flags_a_label_off_a_comment(tmp_path):
    # Arrange: both comments labelled, plus a label on the code line.
    corpus = build_comment_corpus(root=tmp_path, labelled_lines=[1, 2, 3])

    # Act.
    problems = validate_corpora.find_corpus_problems(corpus=corpus)

    # Assert.
    assert problems == [
        "tiny_comments: dev/positives/pos_a.py:2: label is on a line with no comment",
    ]


def test_validator_flags_unlabelled_and_missing_files(tmp_path):
    # Arrange: a fully labelled corpus, then an extra file and a deleted one.
    corpus = build_comment_corpus(root=tmp_path, labelled_lines=[1, 3])
    (corpus / "dev" / "positives" / "pos_b.py").write_text("value = 1\n", encoding="utf-8")
    (corpus / "dev" / "positives" / "pos_a.py").unlink()

    # Act.
    problems = validate_corpora.find_corpus_problems(corpus=corpus)

    # Assert.
    assert "tiny_comments: dev/positives/pos_b.py: corpus file has no label entry" in problems
    assert "tiny_comments: dev/positives/pos_a.py: label names a missing file" in problems


def test_validator_accepts_a_complete_corpus(tmp_path):
    # Arrange: every comment labelled, and the corpus honestly too small.
    corpus = build_comment_corpus(root=tmp_path, labelled_lines=[1, 3])

    # Act.
    problems = validate_corpora.find_corpus_problems(corpus=corpus)

    # Assert.
    assert problems == []


def test_gate_passes_a_drop_of_exactly_the_tolerance():
    # Arrange: holdout recall falls by the tolerance, dev recall collapses.
    baseline = [build_record(rule="X-001", dev_recall=1.0, holdout_recall=0.9)]
    current = [build_record(rule="X-001", dev_recall=0.1, holdout_recall=0.88)]

    # Act.
    outcome = regression_gate.find_regressions(baseline=baseline, current=current)

    # Assert: at the boundary nothing regresses, and dev never gates.
    assert outcome["regressions"] == []


def test_gate_fails_a_drop_beyond_the_tolerance():
    # Arrange: holdout recall falls by 0.03, beyond the 0.02 tolerance.
    baseline = [build_record(rule="X-001", dev_recall=1.0, holdout_recall=0.9)]
    current = [build_record(rule="X-001", dev_recall=1.0, holdout_recall=0.87)]

    # Act.
    outcome = regression_gate.find_regressions(baseline=baseline, current=current)

    # Assert.
    assert outcome["regressions"] == [
        "X-001: holdout recall 0.870 is below the baseline 0.900 minus 0.02",
    ]


def test_gate_skips_a_baseline_without_holdout_and_fails_a_dropped_rule():
    # Arrange: one rule has no holdout baseline, another vanished from the run.
    baseline = [
        build_record(rule="OLD-001", dev_recall=1.0, holdout_recall=None),
        build_record(rule="GONE-001", dev_recall=1.0, holdout_recall=1.0),
    ]

    # Act.
    outcome = regression_gate.find_regressions(baseline=baseline, current=[])

    # Assert.
    assert outcome["skipped"] == ["OLD-001: no holdout numbers in the baseline"]
    assert outcome["regressions"] == ["GONE-001: scored in the baseline but not in this run"]


def test_generated_cases_are_reproducible_and_committed():
    # Arrange / Act: generate twice, and compare against the committed files.
    first = generate_adversarial.build_all_cases()
    second = generate_adversarial.build_all_cases()
    stale = [
        line
        for name, cases in first.items()
        for line in generate_adversarial.find_stale(
            corpus=labelled_corpus.CORPORA_ROOT / name,
            cases=cases,
        )
    ]

    # Assert: the same bytes each run, and the committed files are that output.
    assert first == second
    assert stale == []
