"""Score PROSE-004 (em-dash density) against evals/corpora/prose_em_dash/.

Labels are per document: flag true for a long document that carries an em
dash through most of its sentences. The advisory is a warning reported on
line 1, so the file is the unit.

The corpus is split into dev/ and holdout/ (see evals/README.md); the scorer
reports each split and the gap between them.

Run:
    uv run python evals/score_prose004.py
"""

from __future__ import annotations

from pathlib import Path

from labelled_corpus import ScoreRecord, Site, evaluate_corpus
from metrics_report import run_scorer

from lanorme.checks.prose import ProseCheck
from lanorme.scan import Scan

RULE = "PROSE-004"
CORPUS = "prose_em_dash"


def find_flagged(root: Path) -> set[Site]:
    """Run the em-dash-density advisory on one split and return the documents it fires on."""
    check = ProseCheck(enabled=True, flag_em_dash=False, flag_em_dash_density=True)
    result = check.check(Scan(root=root))
    return {
        (finding.file.replace("\\", "/"), finding.line)
        for finding in result.warnings
        if finding.code == RULE
    }


def score() -> ScoreRecord:
    """Return the combined, dev and holdout metrics and the gap for PROSE-004.

    Raises ValueError if the corpus is stale: a finding on a site that
    labels.json does not cover.
    """
    return evaluate_corpus(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged).record


if __name__ == "__main__":
    raise SystemExit(run_scorer(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged))
