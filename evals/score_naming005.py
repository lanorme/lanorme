"""Score NAMING-005 (short names held across a long span) against evals/corpora/docstrings_vacuous/.

Run directly for a human-readable report:

    uv run python evals/score_naming005.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lanorme.checks.naming_scope import NamingScopeCheck  # noqa: E402

RULE = "NAMING-005"
CORPUS = Path(__file__).resolve().parent / "corpora" / "naming_scope"


def _findings() -> set[str]:
    """Run the check over the corpus and return the flagged 'file:line' keys."""
    check = NamingScopeCheck()
    check.configure(settings={"enabled": True})
    result = check.run(src_root=str(CORPUS))
    return {
        f"{violation.file}:{violation.line}"
        for violation in result.violations
        if violation.code == RULE
    }


def score() -> dict:
    """Return {rule, corpus, tp, fp, fn, tn, precision, recall, f1}.

    Raises ValueError if the corpus is stale: a finding on a definition that
    labels.json does not cover, naming the offending file:line.
    """
    labels = {k: v for k, v in json.loads((CORPUS / "labels.json").read_text()).items()
              if not k.startswith("_")}
    flagged = _findings()

    unknown = sorted(flagged - labels.keys())
    if unknown:
        raise ValueError(f"{RULE} corpus is stale, unlabelled finding at {unknown[0]}")

    tp = sum(1 for key, want in labels.items() if want and key in flagged)
    fp = sum(1 for key, want in labels.items() if not want and key in flagged)
    fn = sum(1 for key, want in labels.items() if want and key not in flagged)
    tn = sum(1 for key, want in labels.items() if not want and key not in flagged)

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "rule": RULE, "corpus": CORPUS.name,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
    }


if __name__ == "__main__":
    report = score()
    print(f"{RULE} on {report['corpus']}: "
          f"P = {report['precision']:.3f} / R = {report['recall']:.3f} / F1 = {report['f1']:.3f}")
    print(f"  TP = {report['tp']}, FP = {report['fp']}, FN = {report['fn']}, TN = {report['tn']}")
