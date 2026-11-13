"""Measure every evidence signal on the labelled packages of one corpus split.

For each original tree the corpus names, scan it once as SHALLOW-001 does,
then compute for each labelled merge candidate the Common Reuse, vocabulary,
sibling and co-change signals. The result is one JSON record per package,
which ``report_separation.py`` turns into the per-signal comparison.

Run (dev first; the holdout only once the signals are fixed):
    uv run python experiments/semantic-merge/harness/measure_packages.py \
        --repositories /path/to/checkouts --split dev \
        --out experiments/semantic-merge/results/packages_dev.json

With ``--candidates`` it measures the unlabelled extension set instead, the
packages ``select_candidates.py`` listed under ``--repositories``.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from boundary_signals import measure_boundaries, read_boundaries
from cochange_signals import History, measure_cochange, read_history
from idle_boundaries import measure_idle_boundaries
from labelled_packages import (
    LabelledPackage,
    OriginalTree,
    group_by_root,
    is_merge_candidate,
    read_candidate_anchors,
    read_labelled_anchors,
    read_original_tree,
)
from member_surfaces import measure_depth, read_surface
from package_records import PackageRecord
from reuse_signals import PackageView, describe_reuse, measure_reuse
from sibling_signals import measure_siblings
from vocabulary_signals import Vocabulary, build_vocabulary, measure_vocabulary

from lanorme.line_counts import count_effective_lines
from lanorme.sources import Module

logger = logging.getLogger(__name__)


class TreeEvidence:
    """Everything the signals read from one original tree, built once."""

    def __init__(self, *, original: OriginalTree, cache: Path | None) -> None:
        self.original = original
        self.modules: dict[str, Module] = {
            module.relative: module
            for found in original.tree.by_directory.values()
            for module in found
        }
        listed = list(self.modules.values())
        self.identifiers: Vocabulary = build_vocabulary(modules=listed, with_prose=False)
        self.prose: Vocabulary = build_vocabulary(modules=listed, with_prose=True)
        self.history: History | None = read_history(original.root, cache=cache)

    def measure(self, *, package: str, members: list[Module]) -> dict[str, object]:
        """Every signal of one package, keyed by signal family."""
        graph = self.original.tree.graph
        view = PackageView(
            package=package,
            members=tuple(members),
            modules=self.modules,
            graph=graph,
        )
        relatives = [member.relative for member in members]
        signals: dict[str, object] = dict(measure_reuse(view=view))
        signals.update(measure_siblings(members=members, package=package, graph=graph))
        for prefix, vocabulary in (("identifier", self.identifiers), ("prose", self.prose)):
            found = measure_vocabulary(vocabulary=vocabulary, members=relatives, package=package)
            signals.update({f"{prefix}_{name}": value for name, value in found.items()})
        signals.update(measure_cochange(history=self.history, members=relatives))
        surfaces = [read_surface(member) for member in members]
        signals.update(measure_depth(surfaces))
        boundaries = read_boundaries(view=view, surfaces=surfaces)
        signals.update(measure_boundaries(boundaries))
        signals.update(
            measure_idle_boundaries(
                boundaries=boundaries,
                scope_co_use=signals["scope_co_use"],
            ),
        )
        return {"signals": signals, "reuse_sentence": describe_reuse(view=view)}


def _measure_package(*, labelled: LabelledPackage, evidence: TreeEvidence) -> PackageRecord | None:
    """The record of one labelled package, or ``None`` when it is not a merge candidate."""
    original = evidence.original
    package = original.find_package(labelled.package)
    if package is None:
        logger.warning("%s is not in its original tree", labelled.identifier)
        return None
    members = original.collect_members(package)
    if not is_merge_candidate(members=members, budget=original.check.merge_budget):
        return None
    record: dict[str, object] = {
        "identifier": labelled.identifier,
        "split": labelled.split,
        "lineage": labelled.lineage,
        "is_shallow": labelled.is_shallow,
        "is_reported": original.is_reported(package),
        "member_count": len(members),
        "lines": sum(count_effective_lines(source=member.source) for member in members),
        "note": labelled.note.split(" (structural replica")[0].split("; verbatim")[0],
    }
    record.update(original.describe_structure(package, members=members))
    record.update(evidence.measure(package=package, members=members))
    return record


def measure_anchors(*, anchors: list[LabelledPackage], cache: Path | None) -> list[PackageRecord]:
    """The records of every merge candidate among *anchors*."""
    records: list[PackageRecord] = []
    for root, labelled in group_by_root(anchors).items():
        if not root.is_dir():
            logger.warning("no checkout at %s; %d packages skipped", root, len(labelled))
            continue
        evidence = TreeEvidence(original=read_original_tree(root), cache=cache)
        found = [_measure_package(labelled=package, evidence=evidence) for package in labelled]
        records.extend(record for record in found if record is not None)
    return sorted(records, key=lambda record: str(record["identifier"]))


def main() -> None:
    """Measure one split and write its records as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repositories", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "holdout"), default=None)
    parser.add_argument("--candidates", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if arguments.candidates is not None:
        anchors = read_candidate_anchors(
            candidates=arguments.candidates,
            trees=arguments.repositories,
        )
    else:
        labelled = read_labelled_anchors(repositories=arguments.repositories)
        anchors = [anchor for anchor in labelled if anchor.split == arguments.split]
    records = measure_anchors(anchors=anchors, cache=arguments.cache)
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(records, indent=1) + "\n", encoding="utf-8")
    logger.info("%d packages written to %s", len(records), arguments.out)


if __name__ == "__main__":
    main()
