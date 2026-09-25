"""Score CMT-007 (vacuous docstrings) against evals/corpora/docstrings_vacuous/.

Labels sit on definition lines: flag true when the docstring says nothing the
signature does not; out-of-scope definitions are labelled false.

The corpus is split into dev/ and holdout/ (see evals/README.md); the scorer
reports each split and the gap between them.

Run:
    uv run python evals/score_cmt007.py
"""

from __future__ import annotations

from pathlib import Path

from lanorme.checks.docstrings import DocstringsCheck
from lanorme.scan import Scan
from labelled_corpus import ScoreRecord, Site, evaluate_corpus
from metrics_report import run_scorer

RULE = "CMT-007"
CORPUS = "docstrings_vacuous"


def find_flagged(root: Path) -> set[Site]:
    """Run the docstrings check on one split and return its CMT-007 sites."""
    check = DocstringsCheck()
    check.configure(settings={"enabled": True})
    result = check.check(Scan(root=root))
    return {
        (finding.file.replace("\\", "/"), finding.line)
        for finding in result.violations
        if finding.code == RULE
    }


def score() -> ScoreRecord:
    """Return the combined, dev and holdout metrics and the gap for CMT-007.

    Raises ValueError if the corpus is stale: a finding on a site that
    labels.json does not cover.
    """
    return evaluate_corpus(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged).record


if __name__ == "__main__":
    raise SystemExit(run_scorer(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged))
