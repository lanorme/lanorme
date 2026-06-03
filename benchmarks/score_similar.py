"""Score SIMILAR-001 against the labelled duplication_similar corpus.

Each corpus file holds exactly two top-level functions. Files under
``positives/`` ARE near-duplicates (the check SHOULD flag the file); files
under ``negatives/`` look similar but are legitimately distinct (the check
SHOULD NOT flag the file).

Scoring is done IN ISOLATION: the similarity check is run against each single
file on its own, so functions in other corpus files never cross-match. A file
is "flagged" when the check emits at least one SIMILAR-001 warning for it.

  positive flagged   -> TP        positive not flagged -> FN
  negative flagged   -> FP        negative not flagged -> TN

Reports precision, recall, F1, the confusion counts, and the misclassified
filenames.

Run:
    uv run python benchmarks/score_similar.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from lanorme.checks.similarity import SimilarityCheck

_CORPUS = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "duplication_similar"
_POSITIVES = _CORPUS / "positives"
_NEGATIVES = _CORPUS / "negatives"


def _flagged(*, file_path: Path) -> bool:
    """Run the similarity check against a single file in isolation.

    The check scans a *directory*, so point it at a throwaway root containing
    only this one file by scanning its parent and keeping only this file's
    warnings. Within-file-only pairing means no cross-file matches occur, so
    filtering by filename reproduces strict per-file isolation.
    """
    check = SimilarityCheck(enabled=True)
    result = check.run(src_root=str(file_path.parent))
    return any(w.file == file_path.name for w in result.warnings)


def _ratio(*, numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def main() -> int:
    if not _POSITIVES.is_dir() or not _NEGATIVES.is_dir():
        print(f"error: corpus not found under {_CORPUS}", file=sys.stderr)
        return 2

    positives = sorted(p for p in _POSITIVES.glob("*.py") if p.name != "__init__.py")
    negatives = sorted(p for p in _NEGATIVES.glob("*.py") if p.name != "__init__.py")

    true_positives = [p for p in positives if _flagged(file_path=p)]
    false_negatives = [p for p in positives if not _flagged(file_path=p)]
    false_positives = [p for p in negatives if _flagged(file_path=p)]
    true_negatives = [p for p in negatives if not _flagged(file_path=p)]

    tp, fp, fn, tn = (
        len(true_positives),
        len(false_positives),
        len(false_negatives),
        len(true_negatives),
    )
    precision = _ratio(numerator=tp, denominator=tp + fp)
    recall = _ratio(numerator=tp, denominator=tp + fn)
    f1 = _ratio(numerator=2 * precision * recall, denominator=precision + recall)

    print("SIMILAR-001 structural near-duplicate detector - evaluation against labelled corpus")
    print(f"corpus: {_CORPUS}")
    print(f"files:  {len(positives)} positives / {len(negatives)} negatives\n")

    print("Confusion counts")
    print(f"  true positives  (TP): {tp}")
    print(f"  false positives (FP): {fp}")
    print(f"  false negatives (FN): {fn}")
    print(f"  true negatives  (TN): {tn}\n")

    print("Metrics")
    print(f"  PRECISION: {precision:.3f}  (TP / (TP + FP))")
    print(f"  RECALL:    {recall:.3f}  (TP / (TP + FN))")
    print(f"  F1:        {f1:.3f}\n")

    print(f"FALSE POSITIVES (negative wrongly flagged) - {fp}")
    if false_positives:
        for path in false_positives:
            print(f"  {path.name}")
    else:
        print("  (none)")
    print()

    print(f"FALSE NEGATIVES (positive missed) - {fn}")
    if false_negatives:
        for path in false_negatives:
            print(f"  {path.name}")
    else:
        print("  (none)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
