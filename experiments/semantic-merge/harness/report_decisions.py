"""Whether Common Reuse evidence in a finding's message changes what an agent decides.

``results/decisions.json`` holds the decisions of agents acting as a project's
maintainer on the findings SHALLOW-001 reports: ``merge`` (follow the fix) or
``keep`` (silence the finding). Each finding was decided four times, twice
with the rule's message as it is (``plain``) and twice with one sentence of
Common Reuse evidence appended (``evidence``, the sentence
``reuse_signals.describe_reuse`` writes); no agent saw both arms. This
reports the share of merge decisions per arm, how often the two replicates of
an arm agree, and the decisions by the finding's blind rating.

Run:
    uv run python experiments/semantic-merge/harness/report_decisions.py \
        --out experiments/semantic-merge/results/decisions_report.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from report_ratings import MERGE, RESULTS, combine_ratings

ARMS = ("plain", "evidence")


def _read_decisions() -> dict[str, dict[str, list[str]]]:
    """Each finding's decisions per arm, in replicate order."""
    decisions: dict[str, dict[str, list[str]]] = {}
    rows = json.loads((RESULTS / "decisions.json").read_text(encoding="utf-8"))
    for row in sorted(rows, key=lambda row: row["replicate"]):
        by_arm = decisions.setdefault(row["identifier"], {arm: [] for arm in ARMS})
        by_arm[row["arm"]].append(row["decision"])
    return decisions


def _read_levels() -> dict[str, str]:
    """Each rated package's combined blind rating."""
    labels: dict[str, dict[str, str]] = {}
    for found in json.loads((RESULTS / "ratings.json").read_text(encoding="utf-8")):
        labels.setdefault(found["identifier"], {})[found["rater"]] = found["label"]
    return {identifier: combine_ratings(found) for identifier, found in labels.items()}


def summarise_decisions(
    *,
    decisions: dict[str, dict[str, list[str]]],
    levels: dict[str, str],
) -> dict[str, object]:
    """Merge decisions per arm, replicate agreement, and merges by blind rating."""
    report: dict[str, object] = {"findings": len(decisions)}
    for arm in ARMS:
        made = [decision for by_arm in decisions.values() for decision in by_arm[arm]]
        by_level: Counter[str] = Counter()
        total: Counter[str] = Counter()
        for identifier, by_arm in decisions.items():
            total[levels[identifier]] += len(by_arm[arm])
            by_level[levels[identifier]] += by_arm[arm].count(MERGE)
        report[arm] = {
            "decisions": len(made),
            "merges": made.count(MERGE),
            "replicates_agreeing": sum(
                1 for by_arm in decisions.values() if len(set(by_arm[arm])) == 1
            ),
            "merges_by_rating": {
                level: f"{by_level[level]} of {total[level]}" for level in sorted(total)
            },
        }
    report["findings_decided_differently"] = sum(
        1
        for by_arm in decisions.values()
        if by_arm["plain"].count(MERGE) != by_arm["evidence"].count(MERGE)
    )
    return report


def main() -> None:
    """Print the decision summary and write it as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()
    report = summarise_decisions(decisions=_read_decisions(), levels=_read_levels())
    print(json.dumps(report, indent=1))
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
