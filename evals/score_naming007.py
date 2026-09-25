"""Score NAMING-007 (a command not led by a verb) against evals/corpora/naming_command/.

Labels sit on definition lines: flag true when a function acts, returns
nothing and does not lead with a verb. The corpus is too small to split, so
every file is in dev/.

The corpus is split into dev/ and holdout/ (see evals/README.md); the scorer
reports each split and the gap between them.

Run:
    uv run python evals/score_naming007.py
"""

from __future__ import annotations

from pathlib import Path

from labelled_corpus import ScoreRecord, Site, evaluate_corpus
from metrics_report import run_scorer

from lanorme.checks.naming_canon import NamingCanonCheck
from lanorme.scan import Scan

RULE = "NAMING-007"
CORPUS = "naming_command"


def find_flagged(root: Path) -> set[Site]:
    """Run the check on one split and return its NAMING-007 sites."""
    result = NamingCanonCheck().check(Scan(root=root))
    return {
        (finding.file.replace("\\", "/"), finding.line)
        for finding in [*result.violations, *result.warnings]
        if finding.code == RULE
    }


def score() -> ScoreRecord:
    """Return the combined, dev and holdout metrics and the gap for NAMING-007.

    Raises ValueError if the corpus is stale: a finding on a site that
    labels.json does not cover.
    """
    return evaluate_corpus(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged).record


if __name__ == "__main__":
    raise SystemExit(run_scorer(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged))
