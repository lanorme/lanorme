"""What the co-location model's package scores say about over-splits.

Reads the scores ``score_packages.py`` wrote and reports three comparisons:

- **Over-splits by construction.** In each tree, a small module's cohesion
  score is the boundary score it would get split one definition per file; a
  small package's boundary score is that of a split its author made. The AUC
  of the first against the second says whether the model tells a mechanical
  over-split from an author's split, with no rater involved.
- **Blind ratings.** Among rated packages, the boundary score of those rated
  merge against the rest, and against those rated keep.
- **The agent A/B arms.** The boundary score of each snapshot, pooled over its
  packages: the arm told to use LaNorme split the same service into two to
  three times the files, so its boundaries should separate more pairs that
  authors keep together.

Run:
    uv run python experiments/semantic-merge/harness/report_package_scores.py \
        --scores scores.json --ratings experiments/semantic-merge/results/ratings.json \
        --out experiments/semantic-merge/results/package_scores_report.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

from report_ratings import KEEP, MERGE, combine_ratings
from separation import Observation, compute_auc, summarise_separation

SMALL_LINES = 200
MIN_DEFINITIONS = 3
_ARM = re.compile(r"^ab-(control|lanorme)-(\d+)-stage(\d+)$")
Scores = list[dict[str, object]]


def _collect_construction(scores: Scores) -> dict[str, dict[str, list[float]]]:
    """Per tree, the cohesion scores of small modules and the boundary scores of small packages."""
    by_tree: dict[str, dict[str, list[float]]] = {}
    for package in scores:
        found = by_tree.setdefault(str(package["tree"]), {"fragmented": [], "authored": []})
        if package["boundary_score"] is not None and package["lines"] <= SMALL_LINES:
            found["authored"].append(package["boundary_score"])
        found["fragmented"].extend(
            module["cohesion_score"]
            for module in package["modules"]
            if module["cohesion_score"] is not None
            and module["lines"] <= SMALL_LINES
            and module["definitions"] >= MIN_DEFINITIONS
        )
    return by_tree


def compare_construction(scores: Scores) -> dict[str, object]:
    """Per tree, fragmented small modules against small packages as their authors split them."""
    by_tree = _collect_construction(scores)
    trees = {
        tree: compute_auc(first=found["fragmented"], second=found["authored"])
        for tree, found in by_tree.items()
        if len(found["fragmented"]) > 1 and len(found["authored"]) > 1
    }
    listed = sorted(trees.values())
    return {
        "trees": len(listed),
        "median": statistics.median(listed),
        "lowest": listed[0],
        "trees_above_half": sum(1 for score in listed if score > 0.5),
        "fragmented_modules": sum(len(found["fragmented"]) for found in by_tree.values()),
        "authored_packages": sum(len(found["authored"]) for found in by_tree.values()),
        "per_tree": trees,
    }


def compare_ratings(*, scores: Scores, ratings: list[dict[str, str]]) -> dict[str, object]:
    """The boundary score of rated packages: merge against the rest, and against keep."""
    labels: dict[str, dict[str, str]] = {}
    for found in ratings:
        labels.setdefault(found["identifier"], {})[found["rater"]] = found["label"]
    by_identifier = {f"{package['tree']}:{package['package']}": package for package in scores}
    rated = [
        (by_identifier[identifier], combine_ratings(found))
        for identifier, found in labels.items()
        if identifier in by_identifier and by_identifier[identifier]["boundary_score"] is not None
    ]
    report: dict[str, object] = {"rated": len(labels), "scored": len(rated)}
    for name, levels in (("merge_against_rest", None), ("merge_against_keep", (MERGE, KEEP))):
        observations = [
            Observation(
                value=float(package["boundary_score"]),
                is_first=level == MERGE,
                lineage=re.sub(r"-stage\d+$", "", str(package["tree"])),
            )
            for package, level in rated
            if levels is None or level in levels
        ]
        report[name] = summarise_separation(observations)
    return report


def compare_arms(scores: Scores) -> dict[str, object]:
    """Each A/B snapshot's boundary score pooled over its packages, by arm."""
    pooled: dict[str, list[tuple[float, int]]] = {}
    for package in scores:
        if _ARM.match(str(package["tree"])) and package["boundary_score"] is not None:
            pooled.setdefault(str(package["tree"]), []).append(
                (package["boundary_score"], package["cross_pairs"]),
            )
    snapshots = {
        tree: sum(score * pairs for score, pairs in found) / sum(pairs for _score, pairs in found)
        for tree, found in pooled.items()
    }
    arms: dict[str, list[float]] = {"control": [], "lanorme": []}
    for tree, score in snapshots.items():
        arms[_ARM.match(tree).group(1)].append(score)
    return {
        "snapshots": snapshots,
        "control_mean": statistics.mean(arms["control"]) if arms["control"] else None,
        "lanorme_mean": statistics.mean(arms["lanorme"]) if arms["lanorme"] else None,
        "auc_lanorme_above_control": compute_auc(first=arms["lanorme"], second=arms["control"]),
    }


def main() -> None:
    """Print the three comparisons for the given scores and write them as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scores", type=Path, action="append", required=True)
    parser.add_argument("--ratings", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()
    scores = [
        package
        for path in arguments.scores
        for package in json.loads(path.read_text(encoding="utf-8"))
    ]
    report: dict[str, object] = {"construction": compare_construction(scores)}
    for path in arguments.ratings:
        ratings = json.loads(path.read_text(encoding="utf-8"))
        report[path.stem] = compare_ratings(scores=scores, ratings=ratings)
    report["arms"] = compare_arms(scores)
    for name, found in report.items():
        shown = {key: value for key, value in found.items() if key not in ("per_tree", "snapshots")}
        print(name, shown)
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
