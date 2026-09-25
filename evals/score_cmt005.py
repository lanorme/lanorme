"""Score CMT-005 (restating comments) against evals/corpora/comments_restating/.

Every comment in every file is labelled: flag true when the comment restates
the adjacent code. The corpus is heavy on adversarial negatives (rationale,
warnings, units, headers, pragmas) so the precision claim is tested.

The corpus is split into dev/ and holdout/ (see evals/README.md); the scorer
reports each split and the gap between them.

Run:
    uv run python evals/score_cmt005.py
"""

from __future__ import annotations

from pathlib import Path

from labelled_corpus import ScoreRecord, Site, evaluate_corpus
from metrics_report import run_scorer

from lanorme.checks.restating import RestatingCheck
from lanorme.scan import Scan

RULE = "CMT-005"
CORPUS = "comments_restating"


def find_flagged(root: Path) -> set[Site]:
    """Run the restating check on one split and return its CMT-005 sites."""
    result = RestatingCheck(enabled=True).check(Scan(root=root))
    return {
        (finding.file.replace("\\", "/"), finding.line)
        for finding in result.violations
        if finding.code == RULE
    }


def score() -> ScoreRecord:
    """Return the combined, dev and holdout metrics and the gap for CMT-005.

    Raises ValueError if the corpus is stale: a finding on a site that
    labels.json does not cover.
    """
    return evaluate_corpus(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged).record


if __name__ == "__main__":
    raise SystemExit(run_scorer(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged))
