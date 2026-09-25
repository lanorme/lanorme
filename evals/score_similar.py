"""Score SIMILAR-001 (near-duplicate functions) against evals/corpora/duplication_similar/.

Each file holds two top-level functions and is labelled as a whole: flag true
when they are near-duplicates. Pairing is within one file only, so running the
check over a split scores every file in isolation.

The corpus is split into dev/ and holdout/ (see evals/README.md); the scorer
reports each split and the gap between them.

Run:
    uv run python evals/score_similar.py
"""

from __future__ import annotations

from pathlib import Path

from labelled_corpus import ScoreRecord, Site, evaluate_corpus
from metrics_report import run_scorer

from lanorme.checks.similarity import SimilarityCheck
from lanorme.scan import Scan

RULE = "SIMILAR-001"
CORPUS = "duplication_similar"


def find_flagged(root: Path) -> set[Site]:
    """Run the similarity check on one split and return the files it warns on."""
    result = SimilarityCheck(enabled=True).check(Scan(root=root))
    return {
        (finding.file.replace("\\", "/"), finding.line)
        for finding in result.warnings
        if finding.code == RULE
    }


def score() -> ScoreRecord:
    """Return the combined, dev and holdout metrics and the gap for SIMILAR-001.

    Raises ValueError if the corpus is stale: a finding on a site that
    labels.json does not cover.
    """
    return evaluate_corpus(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged).record


if __name__ == "__main__":
    raise SystemExit(run_scorer(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged))
