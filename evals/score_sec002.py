"""Score SQL-001 (no raw SQL) against evals/corpora/security_raw_sql/.

Runs every built-in check, keeps SQL-001, and compares it to the line labels:
flag true for a raw SQL string reaching a database call.

The corpus is split into dev/ and holdout/ (see evals/README.md); the scorer
reports each split and the gap between them.

Run:
    uv run python evals/score_sec002.py
"""

from __future__ import annotations

from pathlib import Path

from labelled_corpus import ScoreRecord, Site, evaluate_corpus
from metrics_report import run_scorer

from lanorme import run_all
from lanorme.cli import _load_builtin_checks  # noqa: PLC2701 -- the scorer pins to internals

RULE = "SQL-001"
CORPUS = "security_raw_sql"


def find_flagged(root: Path) -> set[Site]:
    """Run every built-in check on one split and return its SQL-001 sites."""
    _load_builtin_checks()
    prefix = str(root).replace("\\", "/") + "/"
    flagged: set[Site] = set()
    for result in run_all(src_root=str(root)):
        for finding in result.violations:
            if finding.code == RULE:
                path = finding.file.replace("\\", "/").removeprefix(prefix)
                flagged.add((path, finding.line))
    return flagged


def score() -> ScoreRecord:
    """Return the combined, dev and holdout metrics and the gap for SQL-001.

    Raises ValueError if the corpus is stale: a finding on a site that
    labels.json does not cover.
    """
    return evaluate_corpus(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged).record


if __name__ == "__main__":
    raise SystemExit(run_scorer(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged))
