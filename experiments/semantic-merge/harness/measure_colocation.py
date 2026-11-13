"""Build the co-location dataset: pairs of definitions labelled by what their author did.

For each tree, every package with at least two modules that define something
gives two kinds of pair: two definitions of one module (the author kept them
together) and two definitions of sibling modules (the author drew a boundary
between them). Each pair carries the layout-invariant features of
``definition_pairs.py``. Sampling is capped per module, per package and per
tree, so one large module or one large project cannot supply most of the
data, and migration directories and tests are left out.

Run:
    uv run python experiments/semantic-merge/harness/measure_colocation.py \
        --trees /path/to/checkouts --out pairs.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import multiprocessing
import random
from collections import Counter
from itertools import combinations
from pathlib import Path

from boundary_signals import read_boundaries
from definition_pairs import (
    FEATURES,
    Definition,
    PackageDefinitions,
    measure_pair,
    read_definitions,
    read_package_definitions,
)
from labelled_packages import OriginalTree, read_original_tree
from member_surfaces import read_surface
from reuse_signals import PackageView

from lanorme.checks.file_limits import EXCLUDED_DIR_PARTS
from lanorme.line_counts import count_effective_lines
from lanorme.module_graph import PACKAGE_FILE
from lanorme.paths import is_test_file
from lanorme.sources import Module

logger = logging.getLogger(__name__)
MAX_PACKAGES = 60
# Each tree is measured in a process of its own, so its parsed modules are freed with it.
WORKERS = 2
MAX_PAIRS_PER_MODULE = 10
MAX_CROSS_PAIRS = 30
_SEED = 20261113


class TreeDefinitions:
    """One tree's modules, each module's definitions, and the rarity of each term."""

    def __init__(self, original: OriginalTree) -> None:
        self.original = original
        self.modules: dict[str, Module] = {
            module.relative: module
            for found in original.tree.by_directory.values()
            for module in found
        }
        self.by_module: dict[str, list[Definition]] = {
            relative: read_definitions(module)
            for relative, module in self.modules.items()
            if not is_test_file(relative)
        }
        frequency: Counter[str] = Counter()
        total = 0
        for definitions in self.by_module.values():
            for definition in definitions:
                frequency.update(definition.terms.keys())
                total += 1
        self.rarity: dict[str, float] = {
            term: math.log(total / count) for term, count in frequency.items()
        }

    def list_members(self, package: str) -> list[Module]:
        """The modules directly in *package* that define something, ``__init__.py`` left out."""
        return [
            module
            for module in self.original.tree.by_directory.get(package, [])
            if not module.relative.endswith(PACKAGE_FILE) and self.by_module.get(module.relative)
        ]

    def list_packages(self) -> list[str]:
        """Every package with at least two defining modules, outside migration directories."""
        return [
            package
            for package in sorted(self.original.tree.graph.packages)
            if not any(part in EXCLUDED_DIR_PARTS for part in package.split("/"))
            and len(self.list_members(package)) > 1
        ]

    def read_package(self, package: str) -> PackageDefinitions:
        """The definitions of *package* with their references and the scopes that name them."""
        members = self.list_members(package)
        view = PackageView(
            package=package,
            members=tuple(members),
            modules=self.modules,
            graph=self.original.tree.graph,
        )
        surfaces = [read_surface(member) for member in members]
        boundaries = read_boundaries(view=view, surfaces=surfaces)
        return read_package_definitions(boundaries, by_module=self.by_module)


def sample_pairs(
    *,
    found: PackageDefinitions,
    generator: random.Random,
) -> list[tuple[Definition, Definition]]:
    """Capped samples of same-module pairs, then of pairs across the package's modules."""
    by_module: dict[str, list[Definition]] = {}
    for definition in found.definitions:
        by_module.setdefault(definition.module, []).append(definition)
    pairs: list[tuple[Definition, Definition]] = []
    for definitions in by_module.values():
        same = list(combinations(definitions, 2))
        pairs.extend(generator.sample(same, min(MAX_PAIRS_PER_MODULE, len(same))))
    across = [
        (first, second)
        for left, right in combinations(sorted(by_module), 2)
        for first in by_module[left]
        for second in by_module[right]
    ]
    pairs.extend(generator.sample(across, min(MAX_CROSS_PAIRS, len(across))))
    return pairs


def measure_package(*, tree: TreeDefinitions, package: str, name: str) -> list[dict[str, object]]:
    """One row per sampled pair of *package*: its label, its features and its package's size."""
    found = tree.read_package(package)
    members = tree.list_members(package)
    lines = sum(count_effective_lines(source=member.source) for member in members)
    rows: list[dict[str, object]] = []
    for first, second in sample_pairs(found=found, generator=random.Random(f"{_SEED}:{package}")):
        features = measure_pair(found=found, first=first, second=second, rarity=tree.rarity)
        rows.append(
            {
                "tree": name,
                "package": package,
                "same_module": first.module == second.module,
                "package_lines": lines,
                "features": [features[feature] for feature in FEATURES],
            },
        )
    return rows


def measure_tree(root: Path) -> list[dict[str, object]]:
    """The rows of a seeded sample of one tree's packages."""
    tree = TreeDefinitions(read_original_tree(root))
    packages = tree.list_packages()
    generator = random.Random(f"{_SEED}:{root.name}")
    kept = generator.sample(packages, min(MAX_PACKAGES, len(packages)))
    rows = [
        row
        for package in sorted(kept)
        for row in measure_package(tree=tree, package=package, name=root.name)
    ]
    logger.info(
        "%-44s %4d packages, %4d kept, %6d pairs",
        root.name,
        len(packages),
        len(kept),
        len(rows),
    )
    return rows


def main() -> None:
    """Measure every tree under ``--trees`` and write the pairs as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trees", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    roots = sorted(root for base in arguments.trees for root in base.iterdir() if root.is_dir())
    with multiprocessing.Pool(processes=WORKERS, maxtasksperchild=1) as pool:
        rows = [row for found in pool.imap(measure_tree, roots) for row in found]
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(
        json.dumps({"features": list(FEATURES), "pairs": rows}) + "\n",
        encoding="utf-8",
    )
    logger.info("%d pairs written to %s", len(rows), arguments.out)


if __name__ == "__main__":
    main()
