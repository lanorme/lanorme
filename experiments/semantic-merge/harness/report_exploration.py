"""What the two blind-rated sets say together, after the confirmation: exploratory.

Nothing here was fixed in advance. With the first rated set (88 structural
packages) and the confirmation set (122) both in hand, this reports three
things the pre-registered analysis does not:

- **The rater ceiling.** How well one rater's three-level rating predicts
  the other rater's ``merge``, as an AUC. A second reader of the same code is
  the most any signal can be asked to match, so this bounds every signal.
- **Signals across both sets.** Each signal's separation of merge from the
  rest in the first set, in the confirmation set and pooled. A signal worth
  another confirmation keeps its direction in both.
- **Merge preconditions among findings.** Of the packages SHALLOW-001
  reports, how many have a name two members define, or a member some client
  uses as a namespace: there the fix the finding suggests needs renames.

Run:
    uv run python experiments/semantic-merge/harness/report_exploration.py \
        --out experiments/semantic-merge/results/exploration_report.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from package_records import PackageRecord
from report_ratings import CONTESTED, KEEP, MERGE, RESULTS, combine_ratings
from report_separation import SIGNALS, select_populations
from separation import Observation, compute_auc, summarise_separation

_ORDER = {KEEP: 0, CONTESTED: 1, MERGE: 2}
FIRST_SET = ("packages_dev.json", "packages_holdout.json", "packages_extension.json")
CONFIRMATION_SET = ("packages_confirmation.json",)
Labels = dict[str, dict[str, str]]


def _read_records(names: tuple[str, ...]) -> dict[str, PackageRecord]:
    return {
        record["identifier"]: record
        for name in names
        for record in json.loads((RESULTS / name).read_text(encoding="utf-8"))
    }


def _read_labels(name: str) -> Labels:
    labels: Labels = {}
    for found in json.loads((RESULTS / name).read_text(encoding="utf-8")):
        labels.setdefault(found["identifier"], {})[found["rater"]] = found["label"]
    return labels


def measure_ceiling(labels: Labels) -> dict[str, dict[str, float | int]]:
    """How well each rater's rating predicts the other rater's merge, on doubly rated packages."""
    paired = [found for found in labels.values() if len(found) == 2]
    report: dict[str, dict[str, float | int]] = {}
    for target, predictor in (("a", "b"), ("b", "a")):
        merged = [_ORDER[found[predictor]] for found in paired if found[target] == MERGE]
        others = [_ORDER[found[predictor]] for found in paired if found[target] != MERGE]
        report[f"rater_{predictor}_predicting_rater_{target}"] = {
            "auc": compute_auc(first=merged, second=others),
            "merge": len(merged),
            "rest": len(others),
        }
    return report


def _rate(*, records: dict[str, PackageRecord], labels: Labels, structural: bool) -> list[tuple]:
    """Each rated record with its combined level, the structural ones only when asked."""
    rated = [records[identifier] for identifier in labels]
    if structural:
        rated = select_populations(rated)["structural"]
    return [(record, combine_ratings(labels[record["identifier"]])) for record in rated]


def compare_sets(*, first: list[tuple], confirmation: list[tuple]) -> dict[str, dict[str, object]]:
    """Each signal's separation of merge from the rest in both sets and pooled."""
    report: dict[str, dict[str, object]] = {}
    groups = {"first": first, "confirmation": confirmation, "pooled": [*first, *confirmation]}
    for signal in SIGNALS:
        report[signal] = {}
        for name, rated in groups.items():
            observations = [
                Observation(
                    value=float(record["signals"][signal]),
                    is_first=level == MERGE,
                    lineage=re.sub(r"-stage\d+$", "", record["lineage"]),
                )
                for record, level in rated
                if record["signals"].get(signal) is not None
            ]
            report[signal][name] = summarise_separation(observations)
    return report


def count_failed_preconditions(records: list[PackageRecord]) -> dict[str, object]:
    """Among reported packages, those whose merge needs renames."""
    reported = [record for record in records if record["is_reported"]]
    colliding = [record for record in reported if record["signals"]["has_collision"]]
    qualified = [record for record in reported if record["signals"]["has_qualified_use"]]
    either = {record["identifier"] for record in (*colliding, *qualified)}
    return {
        "measured": len(records),
        "reported": len(reported),
        "with_collision": len(colliding),
        "with_namespace_use": len(qualified),
        "with_either": sorted(either),
    }


def main() -> None:
    """Print the exploratory comparisons and write them as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()
    first_records, first_labels = _read_records(FIRST_SET), _read_labels("ratings.json")
    later_records = _read_records(CONFIRMATION_SET)
    later_labels = _read_labels("ratings_confirmation.json")
    report: dict[str, object] = {
        "ceiling_first": measure_ceiling(first_labels),
        "ceiling_confirmation": measure_ceiling(later_labels),
        "signals": compare_sets(
            first=_rate(records=first_records, labels=first_labels, structural=True),
            confirmation=_rate(records=later_records, labels=later_labels, structural=False),
        ),
        "preconditions": count_failed_preconditions(
            [*first_records.values(), *later_records.values()],
        ),
    }
    for name in ("ceiling_first", "ceiling_confirmation", "preconditions"):
        print(name, report[name])
    for signal, found in report["signals"].items():
        shown = "  ".join(
            f"{name} {summary['auc']:.2f} [{summary['interval'][0]:.2f}, {summary['interval'][1]:.2f}]"
            for name, summary in found.items()
            if summary["auc"] is not None and summary["interval"] is not None
        )
        print(f"{signal:<24} {shown}")
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
