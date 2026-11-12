"""Run the Modularization Quality search on whole trees and compare it with the authors' packages.

For each tree, the production modules and the imports between them form the
graph; ``__init__.py`` files that only re-export are left out, since they are
not places code could merge into. The search runs from several random starts.
Reported per tree:

- the Modularization Quality of the authors' own partition (one cluster per
  directory) and of the best partition found;
- how many clusters the best partition has, against the number of modules and
  of directories;
- the agreement between the best partition and the authors' directories;
- the stability of the search: the mean agreement between the local optima of
  different starts;
- for each labelled merge candidate in the tree, the share of its member pairs
  the best partition puts in one cluster.

Run:
    uv run python experiments/semantic-merge/harness/measure_clustering.py \
        --trees /path/to/trees --records results/packages_dev.json \
        --out experiments/semantic-merge/results/clustering.json
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
from collections import Counter
from itertools import combinations
from pathlib import Path

from labelled_packages import OriginalTree, read_original_tree
from modularization_quality import (
    build_neighbours,
    compute_adjusted_rand,
    measure_quality,
    search,
)

from lanorme.module_graph import PACKAGE_FILE
from lanorme.paths import is_test_file

logger = logging.getLogger(__name__)
RESTARTS = 20
_SEED = 20261112
MAX_MODULES = 1500


def build_graph(original: OriginalTree) -> dict[str, Counter[str]]:
    """The undirected import graph of a tree's production modules."""
    tree = original.tree
    nodes = [
        relative
        for relative in sorted(tree.relatives)
        if not is_test_file(relative)
        and not (relative.endswith(PACKAGE_FILE) and tree.reexport_only[relative])
    ]
    kept = set(nodes)
    edges = {
        (source, target)
        for source in nodes
        for target in tree.graph.imports.get(source, ())
        if target in kept
    }
    return build_neighbours(nodes=nodes, edges=edges)


def _measure_agreement(optima: list[dict[str, int]]) -> float | None:
    """The mean adjusted Rand index between the local optima of different starts."""
    scores = [
        compute_adjusted_rand(first=first, second=second)
        for first, second in combinations(optima, 2)
    ]
    return sum(scores) / len(scores) if scores else None


def _measure_grouping(*, best: dict[str, int], package: str) -> float | None:
    """The share of a package's member pairs the best partition puts in one cluster."""
    members = [node for node in best if node.rpartition("/")[0] == package]
    pairs = list(combinations(members, 2))
    if not pairs:
        return None
    return sum(1 for first, second in pairs if best[first] == best[second]) / len(pairs)


def measure_tree(root: Path, *, labelled: dict[str, bool]) -> dict[str, object] | None:
    """The search's result on one tree, or ``None`` when the tree is empty or too large."""
    original = read_original_tree(root)
    neighbours = build_graph(original)
    if not 2 <= len(neighbours) <= MAX_MODULES:
        return None
    directories = sorted({node.rpartition("/")[0] for node in neighbours})
    authors = {node: directories.index(node.rpartition("/")[0]) for node in neighbours}
    optima = search(neighbours=neighbours, restarts=RESTARTS, seed=_SEED)
    best = optima[0]
    groupings = {
        package: {
            "is_shallow": is_shallow,
            "grouped_share": _measure_grouping(best=best, package=package),
        }
        for package, is_shallow in sorted(labelled.items())
    }
    return {
        "tree": root.name,
        "modules": len(neighbours),
        "directories": len(directories),
        "clusters": len(set(best.values())),
        "authors_quality": measure_quality(neighbours=neighbours, cluster_of=authors),
        "best_quality": measure_quality(neighbours=neighbours, cluster_of=best),
        "agreement_with_authors": compute_adjusted_rand(first=best, second=authors),
        "stability": _measure_agreement(optima),
        "packages": groupings,
    }


def summarise(records: list[dict]) -> dict[str, object]:
    """Median agreement and stability, and how far the search groups each label's packages."""
    shares: dict[bool, list[float]] = {True: [], False: []}
    for record in records:
        for found in record["packages"].values():
            if found["grouped_share"] is not None:
                shares[found["is_shallow"]].append(found["grouped_share"])
    return {
        "trees": len(records),
        "median_agreement_with_authors": statistics.median(
            record["agreement_with_authors"] for record in records
        ),
        "median_stability": statistics.median(record["stability"] for record in records),
        "trees_where_search_beats_authors": sum(
            1 for record in records if record["best_quality"] > record["authors_quality"]
        ),
        "mean_grouped_share_over_split": statistics.mean(shares[True]) if shares[True] else None,
        "mean_grouped_share_rest": statistics.mean(shares[False]) if shares[False] else None,
        "packages_over_split": len(shares[True]),
        "packages_rest": len(shares[False]),
    }


def _read_labelled(paths: list[Path]) -> dict[str, dict[str, bool]]:
    """Each origin's labelled merge candidates, from the records of ``measure_packages.py``."""
    labelled: dict[str, dict[str, bool]] = {}
    for path in paths:
        for record in json.loads(path.read_text(encoding="utf-8")):
            origin, _colon, package = record["identifier"].partition(":")
            labelled.setdefault(origin, {})[package] = record["is_shallow"]
    return labelled


def main() -> None:
    """Search every tree under ``--trees`` and write the results as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trees", type=Path, action="append", required=True)
    parser.add_argument("--records", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    labelled = _read_labelled(arguments.records)
    roots = sorted(root for base in arguments.trees for root in base.iterdir() if root.is_dir())
    records = []
    for root in roots:
        found = measure_tree(root, labelled=labelled.get(root.name, {}))
        if found is None:
            logger.info("%s skipped", root.name)
            continue
        records.append(found)
        logger.info(
            "%-34s modules %4d directories %3d clusters %3d agreement %.2f stability %.2f",
            found["tree"],
            found["modules"],
            found["directories"],
            found["clusters"],
            found["agreement_with_authors"],
            found["stability"] or 0.0,
        )
    summary = summarise(records)
    logger.info("%s", summary)
    arguments.out.write_text(
        json.dumps({"summary": summary, "trees": records}, indent=1) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
