"""Score NAMING-011 (every function starts with a verb) against evals/corpora/naming_every_verb/.

Labels sit on definition lines: flag true when a query does not lead with a
verb. The corpus is too small to split, so every file is in dev/.

The corpus is split into dev/ and holdout/ (see evals/README.md); the scorer
reports each split and the gap between them.

Run:
    uv run python evals/score_naming011.py
"""

from __future__ import annotations

from pathlib import Path

from labelled_corpus import ScoreRecord, Site, evaluate_corpus
from metrics_report import run_scorer

from lanorme.checks.naming_clean_code import NamingCleanCodeCheck
from lanorme.scan import Scan

RULE = "NAMING-011"
CORPUS = "naming_every_verb"


def find_flagged(root: Path) -> set[Site]:
    """Run the check on one split and return its NAMING-011 sites."""
    check = NamingCleanCodeCheck()
    check.configure(settings={"enabled": True})
    result = check.check(Scan(root=root))
    return {
        (finding.file.replace("\\", "/"), finding.line)
        for finding in [*result.violations, *result.warnings]
        if finding.code == RULE
    }


def score() -> ScoreRecord:
    """Return the combined, dev and holdout metrics and the gap for NAMING-011.

    Raises ValueError if the corpus is stale: a finding on a site that
    labels.json does not cover.
    """
    return evaluate_corpus(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged).record


if __name__ == "__main__":
    raise SystemExit(run_scorer(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged))
