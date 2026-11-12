"""Co-change evidence for a package: do its members change in the same commits?

Modules that change together are logically coupled even when no import joins
them [@gall1998logical], and the files of one commit predict which other files
a change will need [@zimmermann2005mining]. The Common Closure Principle makes
it a packaging rule: classes that change for the same reasons belong together
[@martin2002agile, ch. 20]. This module reads the history of a git checkout
and measures, for the members of a package:

- **Changed together.** Of the commits that change a member, the share that
  change two or more.
- **Pairwise co-change.** The mean Jaccard similarity of the members' commit
  sets.

Three kinds of commit carry no information about coupling and are left out:
merge commits, commits touching more than ``MAX_COMMIT_FILES`` files (a
reformat, a licence header, a mass rename; omitting large transactions is
common practice, at 25 classes in [@beck2011congruence]), and, for each file,
the commit that added it (a package created in one commit is not thereby
coupled). A
package with fewer than ``MIN_COMMITS`` remaining commits has no co-change
signal: the evidence is optional by construction, absent for a tree that is not
a git checkout and for a package younger than its first few changes.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

MAX_COMMIT_FILES = 30
MIN_COMMITS = 3
_ADDED = "A"
_SEPARATOR = "\x00"


@dataclass(frozen=True)
class History:
    """For each file, the commits that changed it after the one that added it."""

    changes: dict[str, frozenset[str]]

    def find_commits(self, relative: str) -> frozenset[str]:
        """The commits that changed *relative*, its own addition left out."""
        return self.changes.get(relative, frozenset())


def _parse_log(text: str) -> dict[str, list[str]]:
    """Each file's changing commits from ``git log --name-status`` output, additions dropped."""
    changes: dict[str, list[str]] = {}
    for block in text.split(_SEPARATOR)[1:]:
        commit, *entries = [line for line in block.splitlines() if line.strip()]
        if len(entries) > MAX_COMMIT_FILES:
            continue
        for entry in entries:
            status, _tab, path = entry.partition("\t")
            if status != _ADDED:
                changes.setdefault(path, []).append(commit)
    return changes


def read_history(root: Path, *, cache: Path | None = None) -> History | None:
    """The change history of the git checkout at *root*, or ``None`` when it is not one.

    The log of a large repository takes a while to read, so it is kept as JSON
    under *cache* when one is given.
    """
    if not (root / ".git").exists():
        return None
    stored = cache / f"{root.name}.history.json" if cache else None
    if stored is not None and stored.exists():
        loaded = json.loads(stored.read_text(encoding="utf-8"))
        return History(changes={path: frozenset(found) for path, found in loaded.items()})
    command = ["git", "-C", str(root), "log", "--no-merges", "--no-renames", "--name-status"]
    command.append("--format=%x00%H")
    finished = subprocess.run(command, capture_output=True, text=True, check=True)
    changes = _parse_log(finished.stdout)
    if stored is not None:
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_text(json.dumps(changes), encoding="utf-8")
    return History(changes={path: frozenset(found) for path, found in changes.items()})


def measure_pair(*, history: History, first: str, second: str) -> float | None:
    """The Jaccard similarity of two files' commit sets; ``None`` when neither ever changed."""
    left, right = history.find_commits(first), history.find_commits(second)
    union = left | right
    return len(left & right) / len(union) if union else None


def measure_cochange(*, history: History | None, members: list[str]) -> dict[str, float | None]:
    """The co-change signals of one package (``None`` without enough history to measure)."""
    empty: dict[str, float | None] = {"commit_count": None, "changed_together": None}
    empty["cochange_similarity"] = None
    if history is None:
        return empty
    touched: dict[str, int] = {}
    for member in members:
        for commit in history.find_commits(member):
            touched[commit] = touched.get(commit, 0) + 1
    if len(touched) < MIN_COMMITS:
        return {**empty, "commit_count": len(touched)}
    scores = [
        score
        for first, second in combinations(members, 2)
        if (score := measure_pair(history=history, first=first, second=second)) is not None
    ]
    return {
        "commit_count": len(touched),
        "changed_together": sum(1 for count in touched.values() if count > 1) / len(touched),
        "cochange_similarity": sum(scores) / len(scores) if scores else None,
    }
