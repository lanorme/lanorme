"""Can an author's choice to keep two definitions in one module be predicted?

Reads the pairs ``measure_colocation.py`` wrote and reports, with each tree
weighted equally:

- each feature's own AUC for telling same-module pairs from pairs across
  sibling modules, per tree;
- the AUC of the logistic model of ``logistic.py`` when the tree it is
  scored on was left out of the fit (leave one project out), so no project is
  scored by a model that saw it;
- the same on the pairs of small packages only, the size SHALLOW-001 reads.

With ``--fit`` it also fits the model on every tree given and writes it, for
scoring trees it has never seen. With ``--model`` it scores the pairs with a
model fitted elsewhere, with no fitting at all: that is the test.

Run:
    uv run python experiments/semantic-merge/harness/report_colocation.py \
        pairs_dev.json --fit experiments/semantic-merge/results/colocation_model.json \
        --out experiments/semantic-merge/results/colocation_dev.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from dataclasses import asdict
from pathlib import Path

from logistic import Model, fit
from separation import compute_auc

MAX_TRAINING_PAIRS = 400
SMALL_PACKAGE_LINES = 200
_SEED = 20261113
Pairs = list[dict[str, object]]


def _group_by_tree(pairs: Pairs) -> dict[str, Pairs]:
    grouped: dict[str, Pairs] = {}
    for pair in pairs:
        grouped.setdefault(str(pair["tree"]), []).append(pair)
    return grouped


def _measure_auc(*, pairs: Pairs, scores: list[float]) -> float | None:
    """The AUC of *scores* for same-module pairs against pairs across sibling modules."""
    same = [score for pair, score in zip(pairs, scores, strict=True) if pair["same_module"]]
    across = [score for pair, score in zip(pairs, scores, strict=True) if not pair["same_module"]]
    return compute_auc(first=same, second=across)


def _summarise(scores: list[float | None]) -> dict[str, float | int]:
    found = sorted(score for score in scores if score is not None)
    quartiles = statistics.quantiles(found, n=4) if len(found) > 1 else [found[0]] * 3
    return {
        "trees": len(found),
        "median": statistics.median(found),
        "lower_quartile": quartiles[0],
        "upper_quartile": quartiles[2],
        "lowest": found[0],
        "trees_above_half": sum(1 for score in found if score > 0.5),
    }


def measure_features(*, grouped: dict[str, Pairs], features: list[str]) -> dict[str, dict]:
    """Each feature's own AUC per tree, summarised over the trees."""
    report: dict[str, dict] = {}
    for index, feature in enumerate(features):
        scores = [
            _measure_auc(pairs=pairs, scores=[pair["features"][index] for pair in pairs])
            for pairs in grouped.values()
        ]
        report[feature] = _summarise(scores)
    return report


def _draw_training(grouped: dict[str, Pairs]) -> dict[str, Pairs]:
    """A capped, seeded sample of each tree's pairs, so each tree weighs about the same."""
    return {
        tree: random.Random(f"{_SEED}:{tree}").sample(pairs, min(MAX_TRAINING_PAIRS, len(pairs)))
        for tree, pairs in grouped.items()
    }


def fit_model(training: Pairs) -> Model:
    """Fit the co-location model to *training* pairs."""
    return fit(
        rows=[list(pair["features"]) for pair in training],
        labels=[bool(pair["same_module"]) for pair in training],
    )


def score_trees(*, grouped: dict[str, Pairs], models: dict[str, Model]) -> dict[str, dict]:
    """Each tree's AUC under its model, on all its pairs and on those of small packages."""
    report: dict[str, dict] = {}
    for tree, pairs in grouped.items():
        model = models[tree]
        small = [pair for pair in pairs if pair["package_lines"] <= SMALL_PACKAGE_LINES]
        report[tree] = {
            "pairs": len(pairs),
            "auc": _measure_auc(
                pairs=pairs,
                scores=[model.score(pair["features"]) for pair in pairs],
            ),
            "small_pairs": len(small),
            "small_auc": _measure_auc(
                pairs=small,
                scores=[model.score(pair["features"]) for pair in small],
            ),
        }
    return report


def leave_one_out(grouped: dict[str, Pairs]) -> dict[str, Model]:
    """For each tree, the model fitted on every other tree."""
    training = _draw_training(grouped)
    return {
        tree: fit_model(
            [pair for other, pairs in training.items() if other != tree for pair in pairs],
        )
        for tree in grouped
    }


def main() -> None:
    """Report the features and the model on the given pairs, and write the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pairs", type=Path)
    parser.add_argument("--fit", type=Path, default=None)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()
    loaded = json.loads(arguments.pairs.read_text(encoding="utf-8"))
    grouped = _group_by_tree(loaded["pairs"])
    report: dict[str, object] = {
        "features": measure_features(grouped=grouped, features=loaded["features"]),
    }
    if arguments.model is not None:
        stored = json.loads(arguments.model.read_text(encoding="utf-8"))
        frozen = Model(
            **{name: stored[name] for name in ("means", "scales", "weights", "intercept")},
        )
        models = dict.fromkeys(grouped, frozen)
    else:
        models = leave_one_out(grouped)
    report["trees"] = score_trees(grouped=grouped, models=models)
    report["model"] = _summarise([found["auc"] for found in report["trees"].values()])
    report["model_small"] = _summarise([found["small_auc"] for found in report["trees"].values()])
    for feature, found in report["features"].items():
        print(f"{feature:<18} {found}")
    print(f"{'model':<18} {report['model']}")
    print(f"{'model, small':<18} {report['model_small']}")
    if arguments.fit is not None:
        fitted = fit_model([pair for pairs in _draw_training(grouped).values() for pair in pairs])
        stored = {"features": loaded["features"], **asdict(fitted)}
        arguments.fit.write_text(json.dumps(stored, indent=1) + "\n", encoding="utf-8")
        print(
            "weights",
            dict(zip(loaded["features"], [round(w, 3) for w in fitted.weights], strict=True)),
        )
    if arguments.out is not None:
        arguments.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
