"""Score CMT-001 (commented-out code) against evals/corpora/comments_commented_code/.

Every comment in every file is labelled: flag true when the comment is
primarily an executable statement a reader would take for disabled code.

The corpus is split into dev/ and holdout/ (see evals/README.md); the scorer
reports each split and the gap between them.

Run:
    uv run python evals/score_cmt001.py
"""

from __future__ import annotations

from pathlib import Path

from lanorme.checks.comments import CommentsCheck
from lanorme.scan import Scan
from labelled_corpus import ScoreRecord, Site, evaluate_corpus
from metrics_report import run_scorer

RULE = "CMT-001"
CORPUS = "comments_commented_code"


def find_flagged(root: Path) -> set[Site]:
    """Run the comments check on one split and return its CMT-001 sites."""
    result = CommentsCheck().check(Scan(root=root))
    return {
        (finding.file.replace("\\", "/"), finding.line)
        for finding in result.violations
        if finding.code == RULE
    }


def score() -> ScoreRecord:
    """Return the combined, dev and holdout metrics and the gap for CMT-001.

    Raises ValueError if the corpus is stale: a finding on a site that
    labels.json does not cover.
    """
    return evaluate_corpus(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged).record


if __name__ == "__main__":
    raise SystemExit(run_scorer(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged))
