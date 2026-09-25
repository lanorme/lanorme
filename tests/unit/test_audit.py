"""The evaluation harness must be honest: split, complete, reproducible and gated.

``evals/audit.py`` is the release audit trail: it validates every labelled
corpus, scores each rule on its dev and holdout splits, and stamps the run with
version and hardware metadata. These tests run it end to end in accuracy-only
mode (``--no-perf``) against a throwaway output path, so no
``evals/results/v*.json`` is left behind, and pin the pieces that keep the
numbers honest: a file's split is recorded and never moves, every corpus file
and comment is labelled and its label still sits on the line it was written
for, the generated cases are reproducible, and the holdout gate fails on a
holdout edit or on a drop beyond its tolerance, and only then.

Tests follow AAA structure with inline ``# Arrange / # Act / # Assert`` markers.
"""

from __future__ import annotations

import json
import shutil
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


def run_audit(*, args: list[str], audit: Path = _AUDIT) -> subprocess.CompletedProcess[str]:
    """Run the audit harness at *audit* with *args*, inheriting the test environment."""
    return subprocess.run(
        [sys.executable, str(audit), *args],
        capture_output=True,
        text=True,
    )


def write_audit(*, path: Path, accuracy: list[dict], digests: dict | None = None) -> Path:
    """Write a minimal previous-audit JSON holding *accuracy* and holdout *digests*."""
    report = {"accuracy": accuracy, "holdout_digests": digests or {}}
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def copy_evals(*, root: Path) -> Path:
    """Copy the eval harness and its corpora under *root*; return the copied audit.py."""
    target = root / "evals"
    target.mkdir()
    for script in _EVALS.glob("*.py"):
        shutil.copy2(script, target / script.name)
    shutil.copytree(_EVALS / "corpora", target / "corpora")
    return target / "audit.py"


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
    texts = {1: "# x = 1", 2: "value = 2", 3: "# y = 3"}
    document = {
        "rules": ["CMT-001"],
        "unit": "comment",
        "description": "test corpus",
        "files": {
            "dev/positives/pos_a.py": {
                "split": "dev",
                "source": "hand-written",
                "labelled_by": "test",
                "labelled_before_rule": True,
                "labels": [
                    {
                        "line": line,
                        "flag": True,
                        "line_hash": labelled_corpus.hash_line(text=texts[line]),
                    }
                    for line in labelled_lines
                ],
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
        assert (entry["holdout"] is None) == (entry["split"] == "dev_only")
    # Assert: every holdout file of a split corpus has a recorded digest.
    assert len(report["holdout_digests"]["duplication_similar"]) > 20
    assert report["holdout_digests"]["naming_scope"] == {}


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
    report = json.loads(out.read_text(encoding="utf-8"))
    similar = next(entry for entry in report["accuracy"] if entry["rule"] == "SIMILAR-001")
    similar["holdout"]["precision"] += 0.05
    baseline = write_audit(
        path=tmp_path / "previous.json",
        accuracy=report["accuracy"],
        digests=report["holdout_digests"],
    )

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


def test_split_proposal_is_pinned():
    # Arrange: inner paths whose proposal was recorded when the corpora were split.
    pinned = {
        "positives/pos_x.py": "dev",
        "fire_api_overview.md": "holdout",
        "positives/recall-gaps_01_reorder_independent.py": "dev",
        "negatives/alembic/versions/0001_initial.py": "holdout",
    }

    # Act.
    proposed = {name: labelled_corpus.compute_proposed_split(name=name) for name in pinned}

    # Assert: the hash proposal has not changed.
    assert proposed == pinned


def test_a_recorded_dev_split_holds_where_the_name_proposes_holdout():
    # Arrange: tuned dev files whose names hash to holdout.
    tuned = [
        ("naming_verb_class", "dev/positives/pos_action_classes.py"),
        ("naming_weak_verb", "dev/positives/pos_weak_verbs.py"),
        ("naming_scope", "dev/negatives/neg_short_span.py"),
    ]

    # Act: read each file's recorded split and its name's proposal.
    found = [
        (
            labelled_corpus.read_labels(corpus=labelled_corpus.CORPORA_ROOT / name)["files"][path][
                "split"
            ],
            labelled_corpus.compute_proposed_split(name=path.partition("/")[2]),
        )
        for name, path in tuned
    ]

    # Assert: the record keeps them in dev, whatever the hash says, and validates.
    assert found == [("dev", "holdout")] * 3
    assert validate_corpora.find_problems() == []


def test_every_corpus_file_records_the_split_it_sits_in():
    # Arrange: every file of every corpus, with the split its entry records.
    placed: list[tuple[str, str]] = []
    for corpus in sorted(p for p in labelled_corpus.CORPORA_ROOT.iterdir() if p.is_dir()):
        document = labelled_corpus.read_labels(corpus=corpus)
        placed.extend((path, entry.get("split", "")) for path, entry in document["files"].items())

    # Act.
    misplaced = [path for path, split in placed if path.partition("/")[0] != split]

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
    # Arrange: every comment labelled and stamped, in its recorded split.
    corpus = build_comment_corpus(root=tmp_path, labelled_lines=[1, 3])

    # Act.
    problems = validate_corpora.find_corpus_problems(corpus=corpus)

    # Assert.
    assert problems == []


def test_validator_flags_a_file_outside_its_recorded_split(tmp_path):
    # Arrange: the entry records holdout, the file sits in dev.
    corpus = build_comment_corpus(root=tmp_path, labelled_lines=[1, 3])
    document = labelled_corpus.read_labels(corpus=corpus)
    document["files"]["dev/positives/pos_a.py"]["split"] = "holdout"
    labelled_corpus.write_labels(corpus=corpus, document=document)

    # Act.
    problems = validate_corpora.find_corpus_problems(corpus=corpus)

    # Assert.
    assert problems == [
        "tiny_comments: dev/positives/pos_a.py: sits in dev/ but labels.json records it "
        "in holdout/",
    ]


def test_validator_flags_labels_shifted_by_an_inserted_line(tmp_path):
    # Arrange: a comment inserted above, so label 1 lands on another comment.
    corpus = build_comment_corpus(root=tmp_path, labelled_lines=[1, 3])
    target = corpus / "dev" / "positives" / "pos_a.py"
    target.write_text("# note\n# x = 1\nvalue = 2\n# y = 3\n", encoding="utf-8")

    # Act.
    problems = validate_corpora.find_corpus_problems(corpus=corpus)

    # Assert: both shifted labels are named with where their text went.
    assert [problem for problem in problems if "text changed" in problem] == [
        "tiny_comments: dev/positives/pos_a.py:1: the labelled line's text changed since "
        "it was labelled; its text is now on line 2",
        "tiny_comments: dev/positives/pos_a.py:3: the labelled line's text changed since "
        "it was labelled; its text is now on line 4",
    ]


def test_validator_flags_a_labelled_comment_rewritten_in_place(tmp_path):
    # Arrange: the first comment becomes prose; the file still has a comment there.
    corpus = build_comment_corpus(root=tmp_path, labelled_lines=[1, 3])
    target = corpus / "dev" / "positives" / "pos_a.py"
    target.write_text("# a sentence of prose\nvalue = 2\n# y = 3\n", encoding="utf-8")

    # Act.
    problems = validate_corpora.find_corpus_problems(corpus=corpus)

    # Assert: only the line hash catches it.
    assert problems == [
        "tiny_comments: dev/positives/pos_a.py:1: the labelled line's text changed since "
        "it was labelled",
    ]


def test_validator_enforces_label_polarity_by_directory(tmp_path):
    # Arrange: a positives/ file whose only labels are negative, and its twin
    # under negatives/ carrying a positive label.
    corpus = build_comment_corpus(root=tmp_path, labelled_lines=[1, 3])
    (corpus / "dev" / "negatives").mkdir()
    shutil.copy2(corpus / "dev/positives/pos_a.py", corpus / "dev/negatives/neg_a.py")
    document = labelled_corpus.read_labels(corpus=corpus)
    positive = document["files"]["dev/positives/pos_a.py"]
    document["files"]["dev/negatives/neg_a.py"] = json.loads(json.dumps(positive))
    for label in positive["labels"]:
        label["flag"] = False
    labelled_corpus.write_labels(corpus=corpus, document=document)

    # Act.
    problems = validate_corpora.find_corpus_problems(corpus=corpus)

    # Assert.
    assert problems == [
        "tiny_comments: dev/negatives/neg_a.py: a file under negatives/ carries a positive label",
        "tiny_comments: dev/positives/pos_a.py: a file under positives/ has no positive label",
    ]


def test_stamp_fills_what_is_missing_and_never_overwrites(tmp_path):
    # Arrange: no split and no line_hash on one label, a stale hash on the other.
    corpus = build_comment_corpus(root=tmp_path, labelled_lines=[1, 3])
    document = labelled_corpus.read_labels(corpus=corpus)
    entry = document["files"]["dev/positives/pos_a.py"]
    del entry["split"]
    del entry["labels"][0]["line_hash"]
    entry["labels"][1]["line_hash"] = "00000000"
    labelled_corpus.write_labels(corpus=corpus, document=document)

    # Act.
    written = validate_corpora.stamp_corpus(corpus=corpus)
    stamped = labelled_corpus.read_labels(corpus=corpus)["files"]["dev/positives/pos_a.py"]

    # Assert: the proposal and the first hash are written, the stale hash kept.
    assert written == 2
    assert stamped["split"] == labelled_corpus.compute_proposed_split(name="positives/pos_a.py")
    assert stamped["labels"][0]["line_hash"] == labelled_corpus.hash_line(text="# x = 1")
    assert stamped["labels"][1]["line_hash"] == "00000000"


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


def test_gate_fails_when_a_holdout_file_is_deleted_with_its_label(tmp_path):
    # Arrange: a scratch copy of the harness, audited once as the baseline.
    audit = copy_evals(root=tmp_path)
    baseline = tmp_path / "baseline.json"
    first = run_audit(
        args=["--no-perf", "--version", "0.0.0-test", "--output", str(baseline)],
        audit=audit,
    )
    assert first.returncode == 0, first.stderr
    corpus = audit.parent / "corpora" / "duplication_similar"
    document = labelled_corpus.read_labels(corpus=corpus)
    victim = min(path for path in document["files"] if path.startswith("holdout/positives/"))
    (corpus / victim).unlink()
    del document["files"][victim]
    labelled_corpus.write_labels(corpus=corpus, document=document)

    # Act: gate the edited copy against its own baseline.
    result = run_audit(
        args=[
            "--no-perf",
            "--version",
            "0.0.0-test",
            "--output",
            str(tmp_path / "now.json"),
            "--gate",
            str(baseline),
        ],
        audit=audit,
    )

    # Assert: the corpus still validates, and the gate names the removed file.
    assert result.returncode == 1
    assert "corpus:" not in result.stdout
    assert (
        f"gate: HOLDOUT EDIT duplication_similar/{victim}: holdout file was removed since "
        "the baseline" in result.stdout
    )


def test_holdout_digest_changes_when_a_flag_flips():
    # Arrange: one file's content with a positive label, then a flipped one.
    content = b"def f():\n    return 1\n"
    labelled = {"labels": [{"line": 1, "flag": True, "line_hash": "x"}]}
    flipped = {"labels": [{"line": 1, "flag": False, "line_hash": "x"}]}

    # Act.
    before = regression_gate.build_file_digest(content=content, entry=labelled)
    after = regression_gate.build_file_digest(content=content, entry=flipped)
    restamped = regression_gate.build_file_digest(
        content=content,
        entry={"labels": [{"line": 1, "flag": True, "line_hash": "y"}]},
    )

    # Assert: the truth is in the digest; a bookkeeping field is not.
    assert before != after
    assert before == restamped


def test_gate_accepts_a_holdout_edit_recorded_as_a_revision():
    # Arrange: one baseline file changed, and a revision accepting its new digest.
    baseline = {"c": {"holdout/a.py": "old", "holdout/b.py": "same"}}
    current = {"c": {"holdout/a.py": "new", "holdout/b.py": "same"}}
    accepted = {"c/holdout/a.py": {"digest": "new", "reason": "fix a mislabel"}}

    # Act.
    unaccepted = regression_gate.find_holdout_changes(
        baseline=baseline,
        current=current,
        accepted={},
    )
    with_revision = regression_gate.find_holdout_changes(
        baseline=baseline,
        current=current,
        accepted=accepted,
    )

    # Assert.
    assert unaccepted == [
        "c/holdout/a.py: holdout file changed (content or labels) since the baseline",
    ]
    assert with_revision == []


def test_gate_holds_numbers_to_the_best_comparable_audit(tmp_path):
    # Arrange: an older audit with a higher recall, a newer one 0.02 lower, and
    # a third audit over different holdout files that must not count.
    digests = {"c": {"holdout/a.py": "d1"}}
    record = {"rule": "X-001", "corpus": "evals/corpora/c"}
    older = write_audit(
        path=tmp_path / "v0.1.0.json",
        accuracy=[{**record, "holdout": {"precision": 1.0, "recall": 0.9}}],
        digests=digests,
    )
    newer = write_audit(
        path=tmp_path / "v0.2.0.json",
        accuracy=[{**record, "holdout": {"precision": 1.0, "recall": 0.88}}],
        digests=digests,
    )
    other = write_audit(
        path=tmp_path / "v0.0.1.json",
        accuracy=[{**record, "holdout": {"precision": 1.0, "recall": 1.0}}],
        digests={"c": {"holdout/z.py": "d9"}},
    )
    current = [{**record, "holdout": {"precision": 1.0, "recall": 0.87}}]

    # Act.
    outcome = regression_gate.apply_history_gate(
        history=[other, older, newer],
        current={"accuracy": current, "holdout_digests": digests},
        accepted={},
    )

    # Assert: 0.87 passes the latest (0.88) but not the best comparable (0.90).
    assert outcome["gated"] == ["X-001"]
    assert outcome["regressions"] == [
        "X-001: holdout recall 0.870 is below the baseline 0.900 minus 0.02",
    ]
    assert outcome["holdout_changes"] == []


def test_gate_says_so_when_no_rule_was_gated(tmp_path):
    # Arrange: a baseline written before the split, with neither holdout
    # numbers nor digests, like v0.20.0.
    baseline = tmp_path / "v0.20.0.json"
    baseline.write_text(json.dumps({"accuracy": [{"rule": "X-001", "precision": 1.0}]}))

    # Act.
    outcome = regression_gate.apply_history_gate(
        history=[baseline],
        current={
            "accuracy": [build_record(rule="X-001", dev_recall=1.0, holdout_recall=0.5)],
            "holdout_digests": {"c": {"holdout/a.py": "d1"}},
        },
        accepted={},
    )
    lines = regression_gate.format_gate(outcome=outcome)

    # Assert: nothing fails, and the summary says nothing was gated and why.
    assert not regression_gate.is_failing(outcome=outcome)
    assert (
        "gate: NOTE no baseline records holdout digests, so holdout edits were not checked" in lines
    )
    assert any(
        line.startswith("gate: NOTE no rule was gated: none of the 1 baseline") for line in lines
    )


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


def build_file_corpus(*, root: Path, flag: object) -> Path:
    """Create a one-file corpus scored by two rules whose entry carries *flag*."""
    corpus = root / "tiny_pairs"
    (corpus / "dev" / "positives").mkdir(parents=True)
    (corpus / "dev" / "positives" / "pos_a.py").write_text("x = 1\n", encoding="utf-8")
    document = {
        "rules": ["A-001", "B-001"],
        "unit": "file",
        "description": "test corpus",
        "split": "too_small",
        "files": {
            "dev/positives/pos_a.py": {
                "split": "dev",
                "source": "hand-written",
                "labelled_by": "test",
                "labelled_before_rule": True,
                "flag": flag,
            },
        },
    }
    (corpus / "labels.json").write_text(json.dumps(document), encoding="utf-8")
    return corpus


def test_per_rule_flag_is_resolved_for_each_rule(tmp_path):
    # Arrange: one file the two rules label differently.
    corpus = build_file_corpus(root=tmp_path, flag={"A-001": True, "B-001": False})
    document = labelled_corpus.read_labels(corpus=corpus)

    # Act.
    problems = validate_corpora.find_corpus_problems(corpus=corpus)
    expected_a = labelled_corpus.build_expected(document=document, rule="A-001")
    expected_b = labelled_corpus.build_expected(document=document, rule="B-001")

    # Assert: valid, and each rule reads its own label.
    assert problems == []
    assert expected_a == {("dev/positives/pos_a.py", 0): True}
    assert expected_b == {("dev/positives/pos_a.py", 0): False}


def test_per_rule_flag_must_name_every_scored_rule(tmp_path):
    # Arrange: a per-rule flag that leaves one of the corpus's rules out.
    corpus = build_file_corpus(root=tmp_path, flag={"A-001": True})

    # Act.
    problems = validate_corpora.find_corpus_problems(corpus=corpus)

    # Assert: the validator names the missing rule set.
    assert len(problems) == 1
    assert "one boolean for each of ['A-001', 'B-001']" in problems[0]


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
