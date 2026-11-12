"""Run the Modularization Quality search on whole trees and compare it with the authors' packages.

For each tree, the production modules and the imports between them form the
graph. Three kinds of module are left out: ``__init__.py`` files that only
re-export (not places code could merge into), migration directories (as the
rule leaves them out), and modules with no import to or from another module of
the graph. An isolated module gains nothing from any move, so the climb leaves
it in whatever cluster its random start put it: counting it would report the
noise of the starts as instability of the search.

The search runs from several random starts. Reported per tree:

- the Modularization Quality of the authors' own partition (one cluster per
  directory) and of the best partition found;
- how many clusters the best partition has, against the number of modules and
  of directories;
- the agreement between the best partition and the authors' directories
  (undefined when the tree has a single directory);
- the stability of the search: the mean agreement between the local optima of
  different starts;
- for each labelled merge candidate in the tree, the share of its member pairs
  the best partition puts in one cluster.

The summary keeps the A/B snapshots (a dozen modules each) apart from the
repositories, and counts each A/B package once, at its last stage.

Run:
    uv run python experiments/semantic-merge/harness/measure_clustering.py \
        --trees /path/to/snapshots --trees /path/to/checkouts \
        --records results/packages_dev.json --records results/packages_holdout.json \
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
from report_separation import select_populations

from lanorme.checks.file_limits import EXCLUDED_DIR_PARTS
from lanorme.module_graph import PACKAGE_FILE
from lanorme.paths import is_test_file

logger = logging.getLogger(__name__)
RESTARTS = 20
_SEED = 20261112
MAX_MODULES = 1500
_SNAPSHOT_PREFIX = "ab-"


def _is_clustered(*, relative: str, original: OriginalTree) -> bool:
    """True for a production module code could merge into, outside migration directories."""
    if is_test_file(relative):
        return False
    if any(part in EXCLUDED_DIR_PARTS for part in relative.split("/")[:-1]):
        return False
    return not (relative.endswith(PACKAGE_FILE) and original.tree.reexport_only[relative])


def build_graph(original: OriginalTree) -> dict[str, Counter[str]]:
    """The undirected import graph of a tree's production modules, isolated ones dropped."""
    tree = original.tree
    nodes = [
        relative
        for relative in sorted(tree.relatives)
        if _is_clustered(relative=relative, original=original)
    ]
    kept = set(nodes)
    edges = {
        (source, target)
        for source in nodes
        for target in tree.graph.imports.get(source, ())
        if target in kept
    }
    neighbours = build_neighbours(nodes=nodes, edges=edges)
    return {node: linked for node, linked in neighbours.items() if linked}


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


def _measure_groupings(
    *,
    best: dict[str, int],
    labelled: dict[str, bool],
    original: OriginalTree,
) -> dict[str, dict[str, object]]:
    """Each labelled package's grouped share, under its path in the original tree."""
    groupings: dict[str, dict[str, object]] = {}
    for package, is_shallow in sorted(labelled.items()):
        found = original.find_package(package)
        share = None if found is None else _measure_grouping(best=best, package=found)
        groupings[package] = {"is_shallow": is_shallow, "grouped_share": share}
    return groupings


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
    agreement = None
    if len(directories) > 1:
        agreement = compute_adjusted_rand(first=best, second=authors)
    return {
        "tree": root.name,
        "modules": len(neighbours),
        "directories": len(directories),
        "clusters": len(set(best.values())),
        "authors_quality": measure_quality(neighbours=neighbours, cluster_of=authors),
        "best_quality": measure_quality(neighbours=neighbours, cluster_of=best),
        "agreement_with_authors": agreement,
        "stability": _measure_agreement(optima),
        "packages": _measure_groupings(best=best, labelled=labelled, original=original),
    }


def _compute_median(values: list[float | None]) -> float | None:
    found = [value for value in values if value is not None]
    return statistics.median(found) if found else None


def _summarise_group(records: list[dict[str, object]]) -> dict[str, object]:
    """Median agreement and stability of one group of trees."""
    return {
        "trees": len(records),
        "trees_with_several_directories": sum(
            1 for record in records if record["agreement_with_authors"] is not None
        ),
        "median_agreement_with_authors": _compute_median(
            [record["agreement_with_authors"] for record in records],
        ),
        "median_stability": _compute_median([record["stability"] for record in records]),
        "trees_where_search_beats_authors": sum(
            1 for record in records if record["best_quality"] > record["authors_quality"]
        ),
    }


def summarise(records: list[dict[str, object]]) -> dict[str, object]:
    """The snapshots and the repositories apart, and how far the search groups each label."""
    shares: dict[bool, list[float]] = {True: [], False: []}
    for record in records:
        for found in record["packages"].values():
            if found["grouped_share"] is not None:
                shares[found["is_shallow"]].append(found["grouped_share"])
    snapshots = [record for record in records if record["tree"].startswith(_SNAPSHOT_PREFIX)]
    repositories = [record for record in records if record not in snapshots]
    return {
        "snapshots": _summarise_group(snapshots),
        "repositories": _summarise_group(repositories),
        "mean_grouped_share_over_split": statistics.mean(shares[True]) if shares[True] else None,
        "mean_grouped_share_rest": statistics.mean(shares[False]) if shares[False] else None,
        "packages_over_split": len(shares[True]),
        "packages_rest": len(shares[False]),
    }


def _read_labelled(paths: list[Path]) -> dict[str, dict[str, bool]]:
    """Each origin's labelled merge candidates, every A/B package once at its last stage."""
    labelled: dict[str, dict[str, bool]] = {}
    records = [record for path in paths for record in json.loads(path.read_text(encoding="utf-8"))]
    for record in select_populations(records)["latest"]:
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
            "%-34s modules %4d directories %3d clusters %3d stability %.2f",
            found["tree"],
            found["modules"],
            found["directories"],
            found["clusters"],
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
