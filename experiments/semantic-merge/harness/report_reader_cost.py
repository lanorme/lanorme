"""Compare what the same bug fix cost an agent in the split layout and in the flat one.

Reads the rows ``reader_cost_tasks.py check`` wrote: one per bug and layout,
with the tokens, tool calls and time the agent spent and whether the hidden
suite passes after its fix. Each bug is its own control, so the comparison is
paired: for each effort measure, the ratio of split to flat per bug, the
median ratio with a bootstrap interval over bugs, and in how many bugs the
split layout cost more. With a dozen pairs only a consistent difference can
show: under no difference, split costs more in ten or more of twelve pairs
about one time in fifty.

Run:
    uv run python experiments/semantic-merge/harness/report_reader_cost.py \
        --out experiments/semantic-merge/results/reader_cost_report.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "results"
MEASURES = ("tokens", "tool_uses", "duration_ms")
RESAMPLES = 5000
_SEED = 20261114
Rows = list[dict[str, object]]


def pair_rows(rows: Rows) -> dict[str, dict[str, dict[str, object]]]:
    """Each bug's two rows, by layout."""
    paired: dict[str, dict[str, dict[str, object]]] = {}
    for row in rows:
        paired.setdefault(str(row["bug"]), {})[str(row["layout"])] = row
    return {bug: found for bug, found in paired.items() if len(found) == 2}


def compute_interval(ratios: list[float]) -> tuple[float, float]:
    """The 95% bootstrap interval of the median ratio, resampling bugs."""
    generator = random.Random(_SEED)
    medians = sorted(
        statistics.median(generator.choices(ratios, k=len(ratios))) for _index in range(RESAMPLES)
    )
    return medians[int(0.025 * RESAMPLES)], medians[int(0.975 * RESAMPLES) - 1]


def compare_measure(
    *, paired: dict[str, dict[str, dict[str, object]]], measure: str
) -> dict[str, object]:
    """The split-to-flat ratio of one effort measure over the paired bugs."""
    ratios = {
        bug: float(found["split"][measure]) / float(found["flat"][measure])
        for bug, found in paired.items()
    }
    listed = list(ratios.values())
    low, high = compute_interval(listed)
    return {
        "pairs": len(listed),
        "median_ratio": statistics.median(listed),
        "interval": [low, high],
        "split_costs_more": sum(1 for ratio in listed if ratio > 1),
        "median_split": statistics.median(
            float(found["split"][measure]) for found in paired.values()
        ),
        "median_flat": statistics.median(
            float(found["flat"][measure]) for found in paired.values()
        ),
        "ratios": ratios,
    }


def count_fixed(rows: Rows) -> dict[str, str]:
    """Per layout, how many fixes leave the whole hidden suite passing."""
    counts: dict[str, list[int]] = {}
    for row in rows:
        counts.setdefault(str(row["layout"]), []).append(int(row["failed"] == 0))
    return {layout: f"{sum(found)} of {len(found)}" for layout, found in sorted(counts.items())}


def main() -> None:
    """Print the paired comparison and write it as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", type=Path, default=RESULTS / "reader_cost.json")
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()
    rows = json.loads(arguments.rows.read_text(encoding="utf-8"))
    paired = pair_rows(rows)
    report: dict[str, object] = {"fixed": count_fixed(rows)}
    print("fixed", report["fixed"])
    for measure in MEASURES:
        found = compare_measure(paired=paired, measure=measure)
        report[measure] = found
        low, high = found["interval"]
        print(
            f"{measure:<12} split/flat median {found['median_ratio']:.2f} [{low:.2f}, {high:.2f}]"
            f"  split costs more in {found['split_costs_more']} of {found['pairs']}"
            f"  (medians {found['median_split']:.0f} against {found['median_flat']:.0f})",
        )
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
