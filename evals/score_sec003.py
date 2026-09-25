"""Score SECRETPY-001 (no hardcoded secrets) against evals/corpora/security_hardcoded_secrets/.

Line labels: flag true for a hardcoded credential literal; negatives cover
environment lookups, placeholders, annotations, regexes and a test-fixture
carve-out.

The corpus is split into dev/ and holdout/ (see evals/README.md); the scorer
reports each split and the gap between them.

Run:
    uv run python evals/score_sec003.py
"""

from __future__ import annotations

from pathlib import Path

from lanorme.checks.secrets import SecretsCheck
from labelled_corpus import ScoreRecord, Site, evaluate_corpus
from metrics_report import run_scorer

RULE = "SECRETPY-001"
CORPUS = "security_hardcoded_secrets"


def find_flagged(root: Path) -> set[Site]:
    """Run the secrets check on one split and return its SECRETPY-001 sites."""
    result = SecretsCheck().run(src_root=str(root))
    return {
        (finding.file.replace("\\", "/"), finding.line)
        for finding in result.violations
        if finding.code == RULE
    }


def score() -> ScoreRecord:
    """Return the combined, dev and holdout metrics and the gap for SECRETPY-001.

    Raises ValueError if the corpus is stale: a finding on a site that
    labels.json does not cover.
    """
    return evaluate_corpus(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged).record


if __name__ == "__main__":
    raise SystemExit(run_scorer(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged))
