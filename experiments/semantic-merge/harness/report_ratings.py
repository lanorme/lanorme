"""How reliable the over-split label is, and what the signals say against blind ratings.

``results/ratings.json`` holds independent blind ratings of every merge
candidate: each corpus candidate once, each extension candidate twice by two
raters, on three levels (``merge``, ``contested``, ``keep``). No rater saw the
rule, a signal or a corpus label. This reports:

- **Reliability.** Cohen's kappa [@cohen1960coefficient] between the corpus
  label and the blind rating (per split), and between the two raters of the
  extension set. A label two careful raters do not agree on bounds what any
  signal can do.
- **Signals against the ratings.** A package's rating is ``merge`` when any
  rater says so, ``keep`` when every rater says so, else ``contested``. Each
  signal's AUC is reported for merge against keep, merge against contested
  and contested against keep, on every rated package the rule's structural
  conditions pass. Two plain size measures, the package's lines and its lines
  per member, are compared the same way: they are what SHALLOW-001 already
  reads.
- **The rule against the ratings.** How the packages SHALLOW-001 reports are
  rated.

Run:
    uv run python experiments/semantic-merge/harness/report_ratings.py \
        --out experiments/semantic-merge/results/ratings_report.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from package_records import PackageRecord
from report_separation import SIGNALS, select_populations
from separation import Observation, summarise_separation

RESULTS = Path(__file__).resolve().parents[1] / "results"
MERGE, CONTESTED, KEEP = "merge", "contested", "keep"
COMPARISONS = ((MERGE, KEEP), (MERGE, CONTESTED), (CONTESTED, KEEP))
SIZE_MEASURES = ("lines", "lines_per_member")


def compute_kappa(pairs: list[tuple[object, object]]) -> dict[str, float]:
    """The observed agreement of paired ratings and Cohen's kappa."""
    total = len(pairs)
    observed = sum(1 for first, second in pairs if first == second) / total
    left = Counter(first for first, _second in pairs)
    right = Counter(second for _first, second in pairs)
    expected = sum(left[level] * right[level] for level in left) / (total * total)
    kappa = (observed - expected) / (1 - expected) if expected < 1 else 1.0
    return {"pairs": total, "agreement": observed, "kappa": kappa}


def _read_ratings() -> dict[str, dict[str, str]]:
    """Each package's label per rater."""
    ratings: dict[str, dict[str, str]] = {}
    for found in json.loads((RESULTS / "ratings.json").read_text(encoding="utf-8")):
        ratings.setdefault(found["identifier"], {})[found["rater"]] = found["label"]
    return ratings


def _read_records() -> dict[str, dict]:
    records: dict[str, dict] = {}
    for name in ("dev", "holdout", "extension"):
        path = RESULTS / f"packages_{name}.json"
        for record in json.loads(path.read_text(encoding="utf-8")):
            records[record["identifier"]] = record
    return records


def combine_ratings(labels: dict[str, str]) -> str:
    """One level per package: merge if any rater says so, keep if all do, else contested."""
    found = set(labels.values())
    if MERGE in found:
        return MERGE
    return KEEP if found == {KEEP} else CONTESTED


def measure_reliability(
    *,
    ratings: dict[str, dict[str, str]],
    records: dict[str, PackageRecord],
) -> dict[str, dict[str, float]]:
    """Kappa of the corpus label against the blind rating, and between the two blind raters."""
    report: dict[str, dict[str, float]] = {}
    for split in ("dev", "holdout"):
        pairs = [
            (records[identifier]["is_shallow"], labels["a"] == MERGE)
            for identifier, labels in ratings.items()
            if records[identifier]["split"] == split
        ]
        report[f"corpus_{split}_against_blind"] = compute_kappa(pairs)
    both = [labels for labels in ratings.values() if len(labels) > 1]
    report["raters_three_levels"] = compute_kappa([(found["a"], found["b"]) for found in both])
    report["raters_merge_or_not"] = compute_kappa(
        [(found["a"] == MERGE, found["b"] == MERGE) for found in both],
    )
    return report


def _read_measure(*, record: PackageRecord, name: str) -> float | None:
    """A signal of *record*, or one of the two size measures the rule already reads."""
    if name == "lines":
        return record["lines"]
    if name == "lines_per_member":
        return record["lines"] / record["member_count"]
    return record["signals"][name]


def compare_levels(
    *,
    rated: list[tuple[PackageRecord, str]],
    first: str,
    second: str,
) -> dict[str, object]:
    """Every measure's separation of the packages rated *first* from those rated *second*."""
    report: dict[str, object] = {}
    for name in (*SIZE_MEASURES, *SIGNALS):
        observations = [
            Observation(
                value=float(value),
                is_first=level == first,
                lineage=re.sub(r"-stage\d+$", "", record["lineage"]),
            )
            for record, level in rated
            if level in (first, second)
            and (value := _read_measure(record=record, name=name)) is not None
        ]
        report[name] = summarise_separation(observations)
    return report


def main() -> None:
    """Print the reliability and the signals' separation by blind rating, and write both."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()
    ratings, records = _read_ratings(), _read_records()
    report: dict[str, object] = {
        "reliability": measure_reliability(ratings=ratings, records=records),
    }
    for name, found in report["reliability"].items():
        print(f"{name:<30} n={found['pairs']:3d} agreement {found['agreement']:.2f}", end="")
        print(f" kappa {found['kappa']:.2f}")
    rated_records = [records[identifier] for identifier in ratings]
    structural = select_populations(rated_records)["structural"]
    rated = [(record, combine_ratings(ratings[record["identifier"]])) for record in structural]
    report["levels"] = dict(Counter(level for _record, level in rated))
    print(f"structural packages rated: {report['levels']}")
    report["reported_levels"] = dict(
        Counter(level for record, level in rated if record["is_reported"]),
    )
    print(f"of those, reported by SHALLOW-001: {report['reported_levels']}")
    for first, second in COMPARISONS:
        found = compare_levels(rated=rated, first=first, second=second)
        report[f"{first}_against_{second}"] = found
        print(f"{first} against {second}")
        for signal, summary in found.items():
            if summary["auc"] is not None:
                low, high = summary["interval"] or (float("nan"), float("nan"))
                print(f"  {signal:<22} AUC {summary['auc']:.2f} [{low:.2f}, {high:.2f}]", end="")
                print(f"  n={summary['first_count']}/{summary['second_count']}")
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
