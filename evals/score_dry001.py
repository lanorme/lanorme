"""Score DRY-001 (exact structural clones) against evals/corpora/duplication_similar/.

The corpus is shared with SIMILAR-001: each file holds two top-level functions
and is labelled as a whole, flag true when they are near-duplicates. DRY-001
matches only exact clones modulo variable names and string literals, so its
recall against near-duplicate labels is low by design; its precision, and the
generated rename and string-literal cases in holdout/generated/, are the
numbers that measure it. DRY-001 pairs functions across files, so each file is
checked alone in a scratch directory.

The corpus is split into dev/ and holdout/ (see evals/README.md); the scorer
reports each split and the gap between them.

Run:
    uv run python evals/score_dry001.py
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from lanorme.checks.duplication import DuplicationCheck
from labelled_corpus import ScoreRecord, Site, evaluate_corpus
from metrics_report import run_scorer

RULE = "DRY-001"
CORPUS = "duplication_similar"


def find_flagged(root: Path) -> set[Site]:
    """Run the duplication check on each file of one split in isolation."""
    flagged: set[Site] = set()
    for path in sorted(root.rglob("*.py")):
        with tempfile.TemporaryDirectory() as scratch:
            shutil.copy(path, Path(scratch) / path.name)
            result = DuplicationCheck().run(src_root=scratch)
        if any(finding.code == RULE for finding in result.violations):
            flagged.add((path.relative_to(root).as_posix(), 0))
    return flagged


def score() -> ScoreRecord:
    """Return the combined, dev and holdout metrics and the gap for DRY-001.

    Raises ValueError if the corpus is stale: a finding on a file that
    labels.json does not cover.
    """
    return evaluate_corpus(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged).record


if __name__ == "__main__":
    raise SystemExit(run_scorer(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged))
