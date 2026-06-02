"""Shared file discovery for checks: traversal pruning honoured at walk time.

Every check that scans a tree should iterate via :func:`iter_py_files` (or
:func:`iter_files`) instead of calling ``Path.rglob`` directly. Two reasons:

1. A built-in set of never-source directories (``.venv``, ``node_modules``,
   ``__pycache__`` ...) is pruned during the walk, so ``lanorme check .`` does
   not read a virtualenv or build tree out of the box.
2. The user's ``exclude`` globs are honoured at walk time as well, so excluded
   directories are never descended into. The CLI still post-filters violations
   by the same globs as a safety net, but pruning here is what makes a large
   excluded subtree fast rather than merely silent.

The active exclude globs are published by the CLI through :func:`set_excludes`
before any check runs. They are process-global state, mirroring the check
registry, because the ``Check.run(*, src_root)`` protocol carries no config.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

# Directories that are never first-party source. Pruned by basename during the
# walk regardless of configuration, so ``lanorme check .`` is fast by default.
DEFAULT_PRUNE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
    }
)

# Exclude globs published by the CLI for this run. Matched against the path
# relative to the scan root, forward-slashed, the same anchoring the CLI's
# post-filter uses so the two stay consistent.
_active_excludes: tuple[str, ...] = ()


def set_excludes(patterns: tuple[str, ...] | list[str]) -> None:
    """Publish the exclude globs honoured by discovery for the current run."""
    global _active_excludes
    _active_excludes = tuple(patterns)


def active_excludes() -> tuple[str, ...]:
    """Return the exclude globs currently in effect."""
    return _active_excludes


def _excluded(*, relative: str, patterns: tuple[str, ...]) -> bool:
    """True if a forward-slashed relative path matches any exclude glob."""
    return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)


def iter_files(root: Path, *, suffix: str | None = None) -> list[Path]:
    """Walk *root*, pruning default and excluded directories, sorted by path.

    Prunes ``DEFAULT_PRUNE_DIRS`` by basename always and any directory whose
    root-relative path matches an active exclude glob. Files whose relative
    path matches an exclude glob are skipped too (so they are never read). If
    *suffix* is given, only files ending with it are returned.
    """
    patterns = _active_excludes
    root = Path(root)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        # Prune in place so os.walk does not descend pruned subtrees.
        kept: list[str] = []
        for name in dirnames:
            if name in DEFAULT_PRUNE_DIRS:
                continue
            child_rel = (here / name).relative_to(root).as_posix()
            if patterns and _excluded(relative=child_rel, patterns=patterns):
                continue
            kept.append(name)
        dirnames[:] = kept

        for name in filenames:
            if suffix is not None and not name.endswith(suffix):
                continue
            path = here / name
            rel = path.relative_to(root).as_posix()
            if patterns and _excluded(relative=rel, patterns=patterns):
                continue
            found.append(path)
    return sorted(found)


def iter_py_files(root: Path) -> list[Path]:
    """Walk *root* for ``*.py`` files, honouring pruning. Sorted by path."""
    return iter_files(root, suffix=".py")
