"""Score packages and modules with a fitted co-location model.

A package is over-split, by the authors' own standard, when the pairs of
definitions its module boundaries separate look like pairs authors keep in one
module. For every package of a tree this writes:

- the **boundary score**: the mean probability the model gives the package's
  cross-module pairs of being kept in one module;
- for each of its modules, the **cohesion score**: the same mean over the
  module's own pairs.

A module split mechanically, one definition per file, would be a package
whose cross-module pairs are exactly that module's pairs, and the features
do not depend on the layout: so a module's cohesion score is the boundary
score its fragmented self would get. That is the over-split by construction
this script lets ``report_package_scores.py`` test, with no file rewritten.

Run:
    uv run python experiments/semantic-merge/harness/score_packages.py \
        --trees /path/to/checkouts \
        --model experiments/semantic-merge/results/colocation_model.json \
        --out scores.json
"""

from __future__ import annotations

import argparse
import json
import logging
import random
from itertools import combinations
from pathlib import Path

from definition_pairs import FEATURES, Definition, PackageDefinitions, measure_pair
from labelled_packages import read_original_tree
from logistic import Model
from measure_colocation import TreeDefinitions

from lanorme.line_counts import count_effective_lines

logger = logging.getLogger(__name__)
MAX_PAIRS = 200
MAX_PACKAGES = 150
_SEED = 20261113


def read_model(path: Path) -> Model:
    """The model ``report_colocation.py --fit`` wrote."""
    stored = json.loads(path.read_text(encoding="utf-8"))
    if stored["features"] != list(FEATURES):
        raise ValueError(f"{path} was fitted on other features than this harness measures")
    return Model(
        means=tuple(stored["means"]),
        scales=tuple(stored["scales"]),
        weights=tuple(stored["weights"]),
        intercept=stored["intercept"],
    )


class PackageScorer:
    """Scores the pairs of one tree's packages with a fitted model."""

    def __init__(self, *, tree: TreeDefinitions, model: Model) -> None:
        self.tree = tree
        self.model = model

    def _score_pairs(
        self,
        *,
        found: PackageDefinitions,
        pairs: list[tuple[Definition, Definition]],
        key: str,
    ) -> float | None:
        """The mean probability of *pairs* being kept in one module (a capped, seeded sample)."""
        if not pairs:
            return None
        drawn = random.Random(f"{_SEED}:{key}").sample(pairs, min(MAX_PAIRS, len(pairs)))
        scores = [
            self.model.score(
                [
                    measure_pair(found=found, first=first, second=second, rarity=self.tree.rarity)[
                        feature
                    ]
                    for feature in FEATURES
                ],
            )
            for first, second in drawn
        ]
        return sum(scores) / len(scores)

    def score(self, package: str) -> dict[str, object]:
        """The boundary score of *package* and the cohesion score of each of its modules."""
        found = self.tree.read_package(package)
        by_module: dict[str, list[Definition]] = {}
        for definition in found.definitions:
            by_module.setdefault(definition.module, []).append(definition)
        across = [
            (first, second)
            for left, right in combinations(sorted(by_module), 2)
            for first in by_module[left]
            for second in by_module[right]
        ]
        lines = {
            member.relative: count_effective_lines(source=member.source)
            for member in self.tree.list_members(package)
        }
        return {
            "package": package,
            "lines": sum(lines.values()),
            "members": len(by_module),
            "cross_pairs": len(across),
            "boundary_score": self._score_pairs(found=found, pairs=across, key=package),
            "modules": [
                {
                    "module": module,
                    "lines": lines.get(module, 0),
                    "definitions": len(definitions),
                    "cohesion_score": self._score_pairs(
                        found=found,
                        pairs=list(combinations(definitions, 2)),
                        key=module,
                    ),
                }
                for module, definitions in sorted(by_module.items())
            ],
        }


def score_tree(root: Path, *, model: Model, wanted: set[str] | None) -> list[dict[str, object]]:
    """The scores of one tree's packages: the *wanted* ones, or a seeded sample of all."""
    tree = TreeDefinitions(read_original_tree(root))
    packages = tree.list_packages()
    if wanted is not None:
        packages = [package for package in packages if package in wanted]
    elif len(packages) > MAX_PACKAGES:
        packages = sorted(random.Random(f"{_SEED}:{root.name}").sample(packages, MAX_PACKAGES))
    scorer = PackageScorer(tree=tree, model=model)
    return [{"tree": root.name, **scorer.score(package)} for package in packages]


def _read_wanted(paths: list[Path]) -> dict[str, set[str]] | None:
    """The packages to score per tree, from ``identifier`` fields of the given JSON files."""
    if not paths:
        return None
    wanted: dict[str, set[str]] = {}
    for path in paths:
        for record in json.loads(path.read_text(encoding="utf-8")):
            origin, _colon, package = record["identifier"].partition(":")
            wanted.setdefault(origin, set()).add(package)
    return wanted


def main() -> None:
    """Score every tree under ``--trees`` and write the scores as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trees", type=Path, action="append", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--only", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    model = read_model(arguments.model)
    wanted = _read_wanted(arguments.only)
    roots = sorted(root for base in arguments.trees for root in base.iterdir() if root.is_dir())
    scores: list[dict[str, object]] = []
    for root in roots:
        if wanted is not None and root.name not in wanted:
            continue
        found = score_tree(root, model=model, wanted=None if wanted is None else wanted[root.name])
        scores.extend(found)
        logger.info("%-44s %4d packages scored", root.name, len(found))
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(scores, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
