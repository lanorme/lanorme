"""The labelled packages of the SHALLOW-001 corpus, located in their original source trees.

The corpus's third-party cases are structural replicas: bodies are replaced
with neutral code and a project's importers are folded into one
``case_importers.py``, so a replica carries no vocabulary, no real importer
sets and no history. Every signal of this spike is therefore measured on the
original tree: the pinned checkout of the project a case replicates (its
``note`` names ``repo@sha:path``), or the case itself when it is verbatim (the
agent A/B snapshots). Hand-written trap cases hold neutral code by design and
are left out.

A package enters the population when a merge is physically possible: it has at
least two members, as SHALLOW-001 counts them, and they total at most the
rule's merge budget. Its label is the corpus's: true when merging it leaves the
design no worse, false when the split is a deliberate or contested convention
or the package is not a merge candidate at all.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from lanorme.checks.package_members import (
    ScannedTree,
    collect_package_members,
    is_leaf,
    is_reachable,
    is_repeated_sibling_layout,
    read_scanned_tree,
)
from lanorme.checks.merge_targets import PackageMember
from lanorme.checks.package_members import PackageCandidate
from lanorme.checks.shallow_modules import ShallowModulesCheck
from lanorme.line_counts import count_effective_lines
from lanorme.scan import Scan
from lanorme.sources import Module

CORPUS = Path(__file__).resolve().parents[3] / "evals" / "corpora" / "shallow_modules"
PACKAGE_FILE = "__init__.py"
MIN_MEMBERS = 2
_REPLICA = re.compile(r"structural replica of ([\w.-]+)@([\w.]+):(\S+?)\)?$")
_VERBATIM_SOURCE = "mined:lanorme@"
# The standard library is not a checkout this spike pins; its one case is left out.
_UNPINNED_REPOSITORIES = frozenset({"cpython"})


@dataclass(frozen=True)
class LabelledPackage:
    """One labelled package: where its original lives, its label and its lineage."""

    split: str
    origin: str
    root: Path
    package: str
    is_shallow: bool
    note: str

    @property
    def lineage(self) -> str:
        """What a resample treats as one unit: a repository, or one A/B run across its stages."""
        return re.sub(r"-stage\d+$", "", self.origin)

    @property
    def identifier(self) -> str:
        """``origin:package``, unique within the population."""
        return f"{self.origin}:{self.package}"


def _locate_original(*, key: str, note: str, source: str, repositories: Path) -> tuple | None:
    """The (origin name, root, package) of a labelled ``__init__.py``, or ``None`` to leave it out."""
    split, _cases, case, *rest = key.split("/")
    found = _REPLICA.search(note)
    if found is not None:
        repository, _revision, path = found.groups()
        if repository in _UNPINNED_REPOSITORIES:
            return None
        return repository, repositories / repository, path.rsplit("/", 1)[0]
    if not source.startswith(_VERBATIM_SOURCE):
        return None
    return case, CORPUS / split / "cases" / case, "/".join(rest[:-1])


def read_labelled_anchors(*, repositories: Path) -> list[LabelledPackage]:
    """Every labelled package file of the corpus with an original tree, duplicates dropped."""
    labels = json.loads((CORPUS / "labels.json").read_text(encoding="utf-8"))["files"]
    seen: dict[tuple[str, str], LabelledPackage] = {}
    for key, label in sorted(labels.items()):
        if not key.endswith(f"/{PACKAGE_FILE}"):
            continue
        located = _locate_original(
            key=key,
            note=label["note"],
            source=label["source"],
            repositories=repositories,
        )
        if located is None:
            continue
        origin, root, package = located
        found = LabelledPackage(
            split=label["split"],
            origin=origin,
            root=root,
            package=package,
            is_shallow=bool(label["flag"]),
            note=label["note"],
        )
        earlier = seen.get((origin, package))
        if earlier is None or (found.is_shallow and not earlier.is_shallow):
            seen[(origin, package)] = found
    return list(seen.values())


@dataclass
class OriginalTree:
    """One original tree, scanned once: the rule's view of it and what the rule reports."""

    root: Path
    tree: ScannedTree
    check: ShallowModulesCheck
    reported: frozenset[str]

    def find_package(self, package: str) -> str | None:
        """The directory of *package* in this tree; a replica may drop a leading ``src/``."""
        if package in self.tree.by_directory:
            return package
        matches = [
            directory for directory in self.tree.by_directory if directory.endswith(f"/{package}")
        ]
        return matches[0] if len(matches) == 1 else None

    def collect_members(self, package: str) -> list[Module] | None:
        """The modules SHALLOW-001 counts as members of *package*; ``None`` when it is exempt."""
        globs = (*self.check.layer_composition_root, *self.check.port_composition_root)
        return collect_package_members(
            modules=self.tree.by_directory.get(package, []),
            composition_globs=globs,
            tree=self.tree,
        )

    def is_reported(self, package: str) -> bool:
        """True when SHALLOW-001 reports *package* on the original tree."""
        return f"{package}/{PACKAGE_FILE}" in self.reported

    def describe_structure(self, package: str, *, members: list[Module]) -> dict[str, bool]:
        """Which of SHALLOW-001's structural conditions *package* meets on the original tree."""
        candidate = PackageCandidate(
            package=package,
            modules=self.tree.by_directory.get(package, []),
            members=[
                PackageMember(relative=module.relative, lines=0, code_lines=0) for module in members
            ],
        )
        group = frozenset(module.relative for module in candidate.modules)
        return {
            "is_reachable": is_reachable(candidate=candidate, tree=self.tree),
            "is_leaf": is_leaf(candidate=candidate, tree=self.tree),
            "is_repeated_layout": is_repeated_sibling_layout(candidate=candidate, tree=self.tree),
            "closes_cycle": self.tree.graph.find_cycle_through(group=group) is not None,
        }


def read_original_tree(root: Path) -> OriginalTree:
    """Scan *root* once, as SHALLOW-001 does, and record what the rule reports there.

    The parse cache belongs to the scan, which is left active only for the
    read: every signal works from the returned modules, never from a new read.
    """
    check = ShallowModulesCheck(enabled=True)
    with Scan(root=root).activate() as scan:
        tree = read_scanned_tree(scan=scan)
        reported = frozenset(finding.file for finding in check.check(scan).warnings)
    return OriginalTree(root=root, tree=tree, check=check, reported=reported)


def group_by_root(packages: list[LabelledPackage]) -> dict[Path, list[LabelledPackage]]:
    """The packages of each original tree, so a tree is scanned once."""
    grouped: dict[Path, list[LabelledPackage]] = defaultdict(list)
    for package in packages:
        grouped[package.root].append(package)
    return dict(sorted(grouped.items()))


def is_merge_candidate(*, members: list[Module] | None, budget: int) -> bool:
    """True when *members* could merge: at least two of them, within the rule's merge budget."""
    if members is None or len(members) < MIN_MEMBERS:
        return False
    return sum(count_effective_lines(source=module.source) for module in members) <= budget
