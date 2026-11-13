"""Test the idle-boundary hypotheses on the confirmation set, once.

The confirmation set is merge candidates from repositories no earlier step of
this spike read, rated blind by two raters after the algorithm and the
hypotheses of ``SPEC.md`` were committed. This reads
``results/packages_confirmation.json`` and ``results/ratings_confirmation.json``
and reports exactly what was fixed in advance:

- the separation of each pre-registered measure for merge against keep,
  merge against contested and merge against the rest;
- how packages with a member used as a namespace, and without one, are rated;
- the two raters' agreement;
- the same separations when a package counts as merge only if both raters say
  so, as a check that the result does not hang on one rater.

Run:
    uv run python experiments/semantic-merge/harness/report_confirmation.py \
        --out experiments/semantic-merge/results/confirmation_report.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from package_records import PackageRecord
from report_ratings import CONTESTED, KEEP, MERGE, RESULTS, combine_ratings, compute_kappa
from separation import Observation, summarise_separation

MEASURES = (
    "merge_evidence",
    "idle_share",
    "scope_co_use",
    "cochange_similarity",
    "lines_per_member",
    "identifier_cohesion",
    "used_together",
)
REST = "rest"
COMPARISONS = ((MERGE, KEEP), (MERGE, CONTESTED), (MERGE, REST))


def combine_strictly(labels: dict[str, str]) -> str:
    """One level per package: merge only if every rater says so, keep if all do."""
    found = set(labels.values())
    if found == {MERGE}:
        return MERGE
    return KEEP if found == {KEEP} else CONTESTED


def _read_measure(*, record: PackageRecord, name: str) -> float | None:
    if name == "lines_per_member":
        return record["lines"] / record["member_count"]
    return record["signals"][name]


def compare(
    *,
    rated: list[tuple[PackageRecord, str]],
    first: str,
    second: str,
) -> dict[str, object]:
    """Each pre-registered measure's separation of *first* from *second* (or from the rest)."""
    report: dict[str, object] = {}
    for name in MEASURES:
        observations = [
            Observation(value=float(value), is_first=level == first, lineage=record["lineage"])
            for record, level in rated
            if (second == REST or level in (first, second))
            and (value := _read_measure(record=record, name=name)) is not None
        ]
        report[name] = summarise_separation(observations)
    return report


def count_by_namespace_use(rated: list[tuple[PackageRecord, str]]) -> dict[str, dict[str, int]]:
    """The ratings of packages with a member some client uses as a namespace, and without."""
    counts: dict[str, Counter[str]] = {"with": Counter(), "without": Counter()}
    for record, level in rated:
        key = "with" if record["signals"]["has_qualified_use"] else "without"
        counts[key][level] += 1
    return {key: dict(found) for key, found in counts.items()}


def _print_comparisons(
    *,
    title: str,
    rated: list[tuple[PackageRecord, str]],
) -> dict[str, object]:
    print(f"{title}: {dict(Counter(level for _record, level in rated))}")
    report: dict[str, object] = {}
    for first, second in COMPARISONS:
        found = compare(rated=rated, first=first, second=second)
        report[f"{first}_against_{second}"] = found
        print(f"  {first} against {second}")
        for name, summary in found.items():
            if summary["auc"] is not None:
                low, high = summary["interval"] or (float("nan"), float("nan"))
                print(f"    {name:<22} AUC {summary['auc']:.2f} [{low:.2f}, {high:.2f}]", end="")
                print(f"  n={summary['first_count']}/{summary['second_count']}")
    return report


def main() -> None:
    """Print the pre-registered comparisons on the confirmation set and write them."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()
    records = {
        record["identifier"]: record
        for record in json.loads((RESULTS / "packages_confirmation.json").read_text("utf-8"))
    }
    labels: dict[str, dict[str, str]] = {}
    for found in json.loads((RESULTS / "ratings_confirmation.json").read_text("utf-8")):
        labels.setdefault(found["identifier"], {})[found["rater"]] = found["label"]
    paired = [list(found.values()) for found in labels.values() if len(found) == 2]
    report: dict[str, object] = {
        "raters_three_levels": compute_kappa([(first, second) for first, second in paired]),
        "raters_merge_or_not": compute_kappa(
            [(first == MERGE, second == MERGE) for first, second in paired],
        ),
        "raters_keep_or_not": compute_kappa(
            [(first == KEEP, second == KEEP) for first, second in paired],
        ),
    }
    for name in ("raters_three_levels", "raters_merge_or_not", "raters_keep_or_not"):
        print(f"{name:<22} {report[name]}")
    for title, combine in (("any rater", combine_ratings), ("both raters", combine_strictly)):
        rated = [(records[identifier], combine(found)) for identifier, found in labels.items()]
        report[title] = _print_comparisons(title=f"merge if {title}", rated=rated)
        report[f"{title}: namespace use"] = count_by_namespace_use(rated)
        print(f"  namespace use: {report[f'{title}: namespace use']}")
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
