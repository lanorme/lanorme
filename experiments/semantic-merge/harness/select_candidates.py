"""Select unlabelled merge candidates from checkouts the corpus does not cover.

The labelled corpus holds a few dozen merge candidates, too few for a signal's
separation to be stable across its two splits. This lists more, from other
projects, for independent blind labelling: every package that is a merge
candidate (at least two members within SHALLOW-001's merge budget) and that
the rule's structural conditions pass (reachable, leaf, not a repeated layout,
no new cycle), which is the population where only meaning is left to decide.
At most ``--per-tree`` are kept per project, by a seeded draw, so one large
project cannot dominate.

Run:
    uv run python experiments/semantic-merge/harness/select_candidates.py \
        --trees /path/to/checkouts --out candidates.json
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from pathlib import Path

from labelled_packages import OriginalTree, is_merge_candidate, read_original_tree

from lanorme.checks.package_members import is_skipped_package
from lanorme.line_counts import count_effective_lines

_SEED = 20261112


def _read_revision(root: Path) -> str:
    command = ["git", "-C", str(root), "rev-parse", "HEAD"]
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()


def list_candidates(original: OriginalTree) -> list[dict[str, object]]:
    """Every structurally passing merge candidate of one tree, with its members' sizes."""
    found: list[dict[str, object]] = []
    for package in sorted(original.tree.graph.packages):
        if is_skipped_package(package=package, tree=original.tree):
            continue
        members = original.collect_members(package)
        if not is_merge_candidate(members=members, budget=original.check.merge_budget):
            continue
        structure = original.describe_structure(package, members=members)
        passes = structure["is_reachable"] and structure["is_leaf"]
        if not passes or structure["is_repeated_layout"] or structure["closes_cycle"]:
            continue
        found.append(
            {
                "package": package,
                "members": {
                    member.relative.rsplit("/", 1)[-1]: count_effective_lines(source=member.source)
                    for member in members
                },
                "is_reported": original.is_reported(package),
            },
        )
    return found


def main() -> None:
    """Write the selected candidates of every tree under ``--trees`` as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trees", type=Path, required=True)
    parser.add_argument("--per-tree", type=int, default=8)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    selected: list[dict[str, object]] = []
    for root in sorted(path for path in arguments.trees.iterdir() if path.is_dir()):
        candidates = list_candidates(read_original_tree(root))
        generator = random.Random(f"{_SEED}:{root.name}")
        kept = generator.sample(candidates, min(arguments.per_tree, len(candidates)))
        revision = _read_revision(root)
        print(f"{root.name}: {len(candidates)} candidates, {len(kept)} kept")
        selected.extend(
            {"origin": root.name, "revision": revision, **candidate}
            for candidate in sorted(kept, key=lambda candidate: str(candidate["package"]))
        )
    arguments.out.write_text(json.dumps(selected, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
