"""Score SHALLOW-001 (a small package split into many shallow modules) on evals/corpora/shallow_modules/.

The rule judges a package against the rest of its project (who imports each
module, which sibling packages repeat its layout), so the corpus is a set of
small projects: each directory under ``cases/`` (and, in the holdout,
``generated/``) is one project root, and the check runs once per case so cases
that share package names never see each other. Labels are per file: the
``__init__.py`` of a package carries flag true when the package is split into
shallow modules [@ousterhout2018philosophy, ch. 4] and merging it as the fix
says breaks nothing; every other file is false.

The corpus is split into dev/ and holdout/ (see evals/README.md); the scorer
reports each split and the gap between them. A split with no ``cases/`` or
``generated/`` directory scores nothing.

Run:
    uv run python evals/score_shallow001.py
"""

from __future__ import annotations

from pathlib import Path

from labelled_corpus import ScoreRecord, Site, evaluate_corpus
from metrics_report import run_scorer

from lanorme.checks.shallow_modules import ShallowModulesCheck
from lanorme.scan import Scan

RULE = "SHALLOW-001"
CORPUS = "shallow_modules"
# The directories of a split that hold one project root per case.
CASE_GROUPS = ("cases", "generated")


def find_flagged(root: Path) -> set[Site]:
    """Run the check once per case of one split and return the package files it warns on."""
    flagged: set[Site] = set()
    for group in CASE_GROUPS:
        base = root / group
        if not base.is_dir():
            continue
        for case in sorted(path for path in base.iterdir() if path.is_dir()):
            with Scan(root=case).activate() as scan:
                result = ShallowModulesCheck(enabled=True).check(scan)
            flagged |= {
                (f"{group}/{case.name}/{finding.file}", 0)
                for finding in result.warnings
                if finding.code == RULE
            }
    return flagged


def score() -> ScoreRecord:
    """Return the combined, dev and holdout metrics and the gap for SHALLOW-001.

    Raises ValueError if the corpus is stale: a finding on a file that
    labels.json does not cover.
    """
    return evaluate_corpus(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged).record


if __name__ == "__main__":
    raise SystemExit(run_scorer(rule=RULE, corpus_name=CORPUS, find_flagged=find_flagged))
