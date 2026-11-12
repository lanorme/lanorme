"""How well each pairwise signal predicts that two modules share a package, per tree.

The labelled packages are a few dozen; the authors' own grouping of modules
into packages is thousands of decisions per project. Following Beck and
Diehl's congruence study of Java projects [@beck2011congruence], this asks of
each coupling concept how congruent it is with that grouping: given two
production modules, does the signal score a pair in one package above a pair in
two sibling packages (packages with the same parent)? Sibling packages are the
hard comparison, since modules far apart in a tree differ in everything.

It validates the instruments rather than the merge question: a signal that
cannot recover the packages the authors made has no claim to say which modules
belong in one module. The converse does not hold, which is the point of the
labelled comparison.

A direct import is a yes or no, and a rare one: its AUC cannot move far from
0.5 however strongly it tracks packages, so the rate of same-package and of
sibling-package pairs joined by an import is recorded beside it. Migration
directories are left out, as the rule leaves them out: their files import
the same framework and repeat the same words by construction.

Run:
    uv run python experiments/semantic-merge/harness/measure_congruence.py \
        --trees /path/to/checkouts --cache /path/to/cache \
        --out experiments/semantic-merge/results/congruence.json
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import statistics
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from cochange_signals import History, measure_pair, read_history
from labelled_packages import read_original_tree
from separation import compute_auc
from sibling_signals import list_fan_out
from vocabulary_signals import Vocabulary, build_vocabulary

from lanorme.checks.file_limits import EXCLUDED_DIR_PARTS
from lanorme.module_graph import PACKAGE_FILE, ModuleGraph
from lanorme.paths import is_test_file
from lanorme.sources import Module

logger = logging.getLogger(__name__)
MAX_PAIRS = 3000
MAX_PAIRS_PER_PACKAGE = 30
_SEED = 20261112
SIGNALS = ("direct_import", "shared_importers", "fan_out", "vocabulary", "co_change")


@dataclass
class TreeSignals:
    """What the pairwise signals read from one tree."""

    modules: dict[str, Module]
    graph: ModuleGraph
    vocabulary: Vocabulary
    history: History | None

    def _measure_jaccard(self, *, first: frozenset[str], second: frozenset[str]) -> float | None:
        union = first | second
        return len(first & second) / len(union) if union else None

    def _list_importers(self, relative: str) -> frozenset[str]:
        found = self.graph.find_importers(relative)
        return frozenset(importer for importer in found if not is_test_file(importer))

    def measure(self, *, first: str, second: str) -> dict[str, float | None]:
        """Every pairwise signal of two modules (``None`` where one has nothing to compare)."""
        joined = second in self.graph.imports.get(first, ()) or first in self.graph.imports.get(
            second,
            (),
        )
        fan_out = [
            list_fan_out(module=self.modules[relative], package=None, graph=self.graph)
            - {first, second}
            for relative in (first, second)
        ]
        importers = [
            self._list_importers(relative) - {first, second} for relative in (first, second)
        ]
        co_change = None
        if self.history is not None:
            co_change = measure_pair(history=self.history, first=first, second=second)
        return {
            "direct_import": float(joined),
            "shared_importers": self._measure_jaccard(first=importers[0], second=importers[1]),
            "fan_out": self._measure_jaccard(first=fan_out[0], second=fan_out[1]),
            "vocabulary": self.vocabulary.measure_cosine(first=first, second=second),
            "co_change": co_change,
        }


def _group_by_package(modules: dict[str, Module]) -> dict[str, list[str]]:
    """The production modules of each directory; ``__init__.py``, tests and migrations left out."""
    grouped: dict[str, list[str]] = {}
    for relative in sorted(modules):
        directory, _slash, name = relative.rpartition("/")
        is_generated = any(part in EXCLUDED_DIR_PARTS for part in directory.split("/"))
        if name != PACKAGE_FILE and not is_test_file(relative) and directory and not is_generated:
            grouped.setdefault(directory, []).append(relative)
    return grouped


def _draw(*, pairs: list[tuple[str, str]], limit: int, generator: random.Random) -> list[tuple]:
    return generator.sample(pairs, min(limit, len(pairs)))


def sample_pairs(modules: dict[str, Module]) -> tuple[list[tuple], list[tuple]]:
    """Seeded samples of same-package pairs and of pairs across sibling packages.

    Pairs grow with the square of a directory's size, so one flat directory of
    a hundred modules would supply most of a tree's sample. Each directory,
    and each pair of sibling directories, therefore contributes at most
    ``MAX_PAIRS_PER_PACKAGE`` pairs.
    """
    grouped = _group_by_package(modules)
    generator = random.Random(_SEED)
    same = [
        pair
        for members in grouped.values()
        for pair in _draw(
            pairs=list(combinations(members, 2)),
            limit=MAX_PAIRS_PER_PACKAGE,
            generator=generator,
        )
    ]
    by_parent: dict[str, list[str]] = {}
    for directory in grouped:
        by_parent.setdefault(directory.rpartition("/")[0], []).append(directory)
    across = [
        pair
        for siblings in by_parent.values()
        for left, right in combinations(siblings, 2)
        for pair in _draw(
            pairs=[(first, second) for first in grouped[left] for second in grouped[right]],
            limit=MAX_PAIRS_PER_PACKAGE,
            generator=generator,
        )
    ]
    return (
        _draw(pairs=same, limit=MAX_PAIRS, generator=generator),
        _draw(pairs=across, limit=MAX_PAIRS, generator=generator),
    )


def measure_tree(root: Path, *, cache: Path | None) -> dict[str, object] | None:
    """Each signal's AUC for same-package against sibling-package pairs in one tree."""
    original = read_original_tree(root)
    modules = {
        module.relative: module for found in original.tree.by_directory.values() for module in found
    }
    same, across = sample_pairs(modules)
    if not same or not across:
        return None
    signals = TreeSignals(
        modules=modules,
        graph=original.tree.graph,
        vocabulary=build_vocabulary(modules=list(modules.values()), with_prose=False),
        history=read_history(root, cache=cache),
    )
    measured = [
        [signals.measure(first=first, second=second) for first, second in pairs]
        for pairs in (same, across)
    ]
    record: dict[str, object] = {"tree": root.name, "same": len(same), "across": len(across)}
    for name in SIGNALS:
        first, second = (
            [found[name] for found in group if found[name] is not None] for group in measured
        )
        record[name] = compute_auc(first=first, second=second)
        record[f"{name}_coverage"] = (len(first) + len(second)) / (len(same) + len(across))
    for group, found in zip(("same", "across"), measured, strict=True):
        record[f"direct_import_rate_{group}"] = statistics.mean(
            pair["direct_import"] for pair in found
        )
    return record


def main() -> None:
    """Measure every tree under ``--trees`` and write the per-tree and median AUCs."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trees", type=Path, action="append", required=True)
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    roots = sorted(root for base in arguments.trees for root in base.iterdir() if root.is_dir())
    found = (measure_tree(root, cache=arguments.cache) for root in roots)
    records = [record for record in found if record is not None]
    summary = {}
    for name in SIGNALS:
        scores = [record[name] for record in records if record[name] is not None]
        summary[name] = {
            "median": statistics.median(scores),
            "lowest": min(scores),
            "highest": max(scores),
            "trees": len(scores),
            "median_coverage": statistics.median(record[f"{name}_coverage"] for record in records),
        }
        logger.info("%-17s %s", name, summary[name])
    for group in ("same", "across"):
        key = f"direct_import_rate_{group}"
        summary[key] = statistics.median(record[key] for record in records)
    arguments.out.write_text(
        json.dumps({"summary": summary, "trees": records}, indent=1) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
