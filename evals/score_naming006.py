"""Score NAMING-006 (a class named as an action) against evals/corpora/naming_verb_class/.

Labels sit on class lines: flag true when the class is named as an action.
The corpus is too small to split, so every file is in dev/.

The corpus is split into dev/ and holdout/ (see evals/README.md); the scorer
reports each split and the gap between them.

Run:
    uv run python evals/score_naming006.py
"""

from __future__ import annotations

from pathlib import Path

from lanorme.checks.naming_canon import NamingCanonCheck
from labelled_corpus import ScoreRecord, Site, evaluate_corpus
from metrics_report import run_scorer

RULE = "NAMING-006"
CORPUS = "naming_verb_class"


def find_flagged(root: Path) -> set[Site]:
    """Run the check on one split and return its NAMING-006 sites."""
    result = NamingCanonCheck().run(src_root=str(root))
    return {
        (finding.file.replace("\\", "/"), finding.line)
        for finding in [*result.violations, *result.warnings]
        if finding.code == RULE
    }


def score() -> ScoreRecord:
    """Return the combined, dev and holdout metrics and the gap for NAMING-006.

    Raises ValueError if the corpus is stale: a finding on a site that
    labels.json does not cover.
    """
    return evaluate_corpus(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged).record


if __name__ == "__main__":
    raise SystemExit(run_scorer(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged))
