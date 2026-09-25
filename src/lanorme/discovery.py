"""Shared file discovery for checks: traversal pruning honoured at walk time.

A check walks its tree through the :class:`lanorme.scan.Scan` it receives
(``scan.py_files()`` / ``scan.files()``), which delegates here; a helper handed
a bare directory calls :func:`iter_py_files` (or :func:`iter_files`) directly.
Neither uses ``Path.rglob``, for two reasons:

1. A built-in set of never-source directories (``.venv``, ``node_modules``,
   ``__pycache__`` ...) is pruned during the walk, so ``lanorme check .`` does
   not read a virtualenv or build tree out of the box.
2. The user's ``exclude`` globs are honoured at walk time as well, so excluded
   directories are never descended into. The CLI still post-filters violations
   by the same globs as a safety net, but pruning here is what makes a large
   excluded subtree fast rather than merely silent.

The exclude globs in force come from the active :class:`lanorme.scan.Scan`,
which the runner activates around each check through :func:`scoped_excludes`.
A caller with no scan (a test driving a check's deprecated ``run(*,
src_root)`` directly) can still publish globs for the process through
:func:`set_excludes`; the scan's globs take precedence while one is active.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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

# Exclude globs published for the process, the fallback when no scan is active.
# Matched against the path relative to the scan root, forward-slashed, the same
# anchoring the CLI's post-filter uses so the two stay consistent.
_active_excludes: tuple[str, ...] = ()

# The globs of the scan the runner has activated, if any; they shadow the
# process-wide ones for the duration of that scan.
_scoped_excludes: ContextVar[tuple[str, ...] | None] = ContextVar(
    "lanorme_scoped_excludes", default=None
)


def set_excludes(patterns: tuple[str, ...] | list[str]) -> None:
    """Publish the process-wide exclude globs, used when no scan is active."""
    global _active_excludes
    _active_excludes = tuple(patterns)


@contextmanager
def scoped_excludes(patterns: tuple[str, ...]) -> Iterator[None]:
    """Make *patterns* the excludes in effect for the duration of the block."""
    token = _scoped_excludes.set(tuple(patterns))
    try:
        yield
    finally:
        _scoped_excludes.reset(token)


def active_excludes() -> tuple[str, ...]:
    """Return the exclude globs currently in effect."""
    scoped = _scoped_excludes.get()
    return _active_excludes if scoped is None else scoped


def _excluded(*, relative: str, patterns: tuple[str, ...]) -> bool:
    """True if a forward-slashed relative path matches any exclude glob."""
    return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)


def iter_files(
    root: Path, *, suffix: str | None = None, excludes: tuple[str, ...] | None = None
) -> list[Path]:
    """Walk *root*, pruning default and excluded directories, sorted by path.

    Prunes ``DEFAULT_PRUNE_DIRS`` by basename always and any directory whose
    root-relative path matches an exclude glob. Files whose relative path
    matches an exclude glob are skipped too (so they are never read). If
    *suffix* is given, only files ending with it are returned. *excludes*
    defaults to the globs in effect (:func:`active_excludes`).
    """
    patterns = active_excludes() if excludes is None else tuple(excludes)
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
