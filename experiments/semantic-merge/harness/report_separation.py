"""Compare every signal between over-split packages and the rest, on measured records.

Reads the records ``measure_packages.py`` wrote and reports, per signal, how
well it separates the packages labelled over-split (first group) from the
others (second group), in four populations:

- ``candidates``: every labelled merge candidate.
- ``structural``: the candidates SHALLOW-001's structural conditions pass
  (reachable, leaf, not a repeated layout, no new cycle), where only meaning
  is left to tell the two groups apart.
- ``reported``: the packages SHALLOW-001 reports on the original trees, so the
  first group is its true positives and the second its false positives.
- ``latest``: the candidates with each A/B run's package counted once, at its
  last stage, as a check that repeated stages do not carry the result.

Run:
    uv run python experiments/semantic-merge/harness/report_separation.py \
        experiments/semantic-merge/results/packages_dev.json \
        --out experiments/semantic-merge/results/separation_dev.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from separation import Observation, summarise_separation

SIGNALS = (
    "internal_density",
    "connected_share",
    "importer_similarity",
    "used_together",
    "mean_share_used",
    "private_share",
    "fan_out_similarity",
    "role_similarity",
    "identifier_cohesion",
    "identifier_lift",
    "identifier_overlap",
    "prose_cohesion",
    "prose_lift",
    "changed_together",
    "cochange_similarity",
)
_STAGE = re.compile(r"-stage(\d+):")


def _is_structural(record: dict) -> bool:
    return (
        record["is_reachable"]
        and record["is_leaf"]
        and not record["is_repeated_layout"]
        and not record["closes_cycle"]
    )


def _keep_latest_stage(records: list[dict]) -> list[dict]:
    """Each A/B run's package once, at its last stage; every other record as it is."""
    latest: dict[str, dict] = {}
    for record in records:
        key = _STAGE.sub(":", record["identifier"])
        found = _STAGE.search(record["identifier"])
        stage = int(found.group(1)) if found else 0
        if key not in latest or stage > latest[key]["stage"]:
            latest[key] = {"stage": stage, "record": record}
    return [entry["record"] for entry in latest.values()]


def select_populations(records: list[dict]) -> dict[str, list[dict]]:
    """The four populations a signal is compared in."""
    return {
        "candidates": records,
        "structural": [record for record in records if _is_structural(record)],
        "reported": [record for record in records if record["is_reported"]],
        "latest": _keep_latest_stage(records),
    }


def compare_signal(*, records: list[dict], signal: str) -> dict[str, object]:
    """One signal's separation of over-split packages from the rest of *records*."""
    observations = [
        Observation(
            value=float(record["signals"][signal]),
            is_first=record["is_shallow"],
            lineage=record["lineage"],
        )
        for record in records
        if record["signals"][signal] is not None
    ]
    return summarise_separation(observations)


def _format_row(*, signal: str, found: dict[str, object]) -> str:
    if found["auc"] is None:
        return f"  {signal:<22} no package in one of the groups"
    interval = found["interval"]
    shown = f"[{interval[0]:.2f}, {interval[1]:.2f}]" if interval else "[      -     ]"
    return (
        f"  {signal:<22} AUC {found['auc']:.2f} {shown}  "
        f"over-split {found['first_median']:.2f} (n={found['first_count']})  "
        f"rest {found['second_median']:.2f} (n={found['second_count']})"
    )


def main() -> None:
    """Print each population's comparison and write all of them as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()
    records = json.loads(arguments.records.read_text(encoding="utf-8"))
    report: dict[str, dict[str, object]] = {}
    for name, population in select_populations(records).items():
        shallow = sum(1 for record in population if record["is_shallow"])
        print(f"{name}: {shallow} over-split, {len(population) - shallow} rest")
        report[name] = {}
        for signal in SIGNALS:
            found = compare_signal(records=population, signal=signal)
            report[name][signal] = found
            print(_format_row(signal=signal, found=found))
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
