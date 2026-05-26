"""Score SEC-002 (no raw SQL) against the labeled evaluation corpus.

Runs every enabled check over the labeled corpus under
``tests/fixtures/security_raw_sql/``, filters to violations whose ``rule``
starts with ``SEC-002`` (so any future sub-codes are picked up too), and
compares them against the ground-truth labels in ``labels.json``.

The corpus deliberately mixes seven raw-SQL shapes against twelve adversarial
negative categories (ORM expressions, parameterized executes, SQL in
docstrings / comments / log messages / regex / changelogs / test assertions,
``.execute`` on non-DB objects, identifiers containing SQL words, and
Alembic migrations under ``alembic/versions/``).

Run:
    uv run python benchmarks/score_sec002.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from lanorme import run_all
from lanorme.cli import _load_builtin_checks  # noqa: PLC2701 -- benchmarks pin to internals

_CORPUS = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "security_raw_sql"
_LABELS = _CORPUS / "labels.json"

_RAW_SQL = "raw_sql"
_OK = "ok"


def _load_labels() -> dict[tuple[str, int], dict[str, str]]:
    """Map (relative_file, line) -> {label, note} for every labeled site."""
    raw = json.loads(_LABELS.read_text(encoding="utf-8"))
    labels: dict[tuple[str, int], dict[str, str]] = {}
    for rel_file, entries in raw.items():
        for entry in entries:
            labels[(rel_file, int(entry["line"]))] = entry
    return labels


def _flagged_sec002() -> set[tuple[str, int]]:
    """Return (file, line) for every violation whose rule starts with SEC-002."""
    _load_builtin_checks()
    results = run_all(src_root=str(_CORPUS))
    flagged: set[tuple[str, int]] = set()
    for result in results:
        for v in result.violations:
            if v.rule.startswith("SEC-002"):
                flagged.add((_relative(v.file), v.line))
    return flagged


def _relative(file: str) -> str:
    """Turn an absolute path emitted by a check into a corpus-relative slash path."""
    norm = file.replace("\\", "/")
    corpus_str = str(_CORPUS).replace("\\", "/")
    if norm.startswith(corpus_str + "/"):
        return norm[len(corpus_str) + 1 :]
    return norm


def _ratio(*, numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def main() -> int:
    if not _LABELS.is_file():
        print(f"error: labels file not found at {_LABELS}", file=sys.stderr)
        return 2

    labels = _load_labels()
    positives = {key for key, entry in labels.items() if entry["label"] == _RAW_SQL}
    negatives = {key for key, entry in labels.items() if entry["label"] == _OK}
    flagged = _flagged_sec002()

    unlabeled = sorted(flagged - set(labels))
    if unlabeled:
        print("error: SEC-002 flagged lines that are not in labels.json:", file=sys.stderr)
        for rel_file, line in unlabeled:
            print(f"  {rel_file}:{line}", file=sys.stderr)
        print(
            "Add these lines to labels.json (or remove the fixture) before scoring.",
            file=sys.stderr,
        )
        return 2

    true_positives = sorted(flagged & positives)
    false_positives = sorted(flagged & negatives)
    false_negatives = sorted(positives - flagged)

    tp, fp, fn = len(true_positives), len(false_positives), len(false_negatives)
    precision = _ratio(numerator=tp, denominator=tp + fp)
    recall = _ratio(numerator=tp, denominator=tp + fn)
    f1 = _ratio(numerator=2 * precision * recall, denominator=precision + recall)

    print("SEC-002 raw-SQL detector -- evaluation against labeled corpus")
    print(f"corpus: {_CORPUS}")
    print(
        f"labels: {len(labels)} sites "
        f"({len(positives)} raw_sql / {len(negatives)} ok)\n"
    )

    print("Confusion counts")
    print(f"  true positives  (TP): {tp}")
    print(f"  false positives (FP): {fp}")
    print(f"  false negatives (FN): {fn}")
    print(f"  true negatives  (TN): {len(negatives) - fp}\n")

    print("Metrics")
    print(f"  PRECISION: {precision:.3f}  (TP / (TP + FP))")
    print(f"  RECALL:    {recall:.3f}  (TP / (TP + FN))")
    print(f"  F1:        {f1:.3f}\n")

    print(f"FALSE POSITIVES (flagged but labeled 'ok') -- {fp}")
    if false_positives:
        for rel_file, line in false_positives:
            note = labels[(rel_file, line)].get("note", "")
            print(f"  {rel_file}:{line}  {note}")
    else:
        print("  (none) -- the detector flagged no benign site in the corpus")
    print()

    print(f"FALSE NEGATIVES (labeled 'raw_sql' but missed) -- {fn}")
    if false_negatives:
        for rel_file, line in false_negatives:
            note = labels[(rel_file, line)].get("note", "")
            print(f"  {rel_file}:{line}  {note}")
    else:
        print("  (none) -- the detector caught every labeled raw-SQL site")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
