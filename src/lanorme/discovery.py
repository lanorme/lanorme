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

The exclude globs and the subtree scope come from the current
:class:`~lanorme.scan.Scan`, which the runner activates around each pass
because the ``Check.run(*, src_root)`` protocol carries no run context.
:func:`set_excludes` and :func:`set_scope` remain for callers that drive a
walk by hand; they replace the current scan's fields until it is replaced.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from lanorme.scan import get_current_scan, install_scan

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
    },
)


def set_scope(prefix: str) -> None:
    """Confine the walk to *prefix* (a root-relative posix directory, or ``""``)."""
    install_scan(replace(get_current_scan(), scope=prefix))


def get_active_scope() -> str:
    """The directory the walk is currently confined to (``""`` for the whole tree)."""
    return get_current_scan().scope


def find_narrower_scope(*, outer: str, inner: str) -> str | None:
    """The directory both scopes confine to, or ``None`` when they are disjoint.

    ``""`` is the whole tree, so it defers to the other scope; otherwise the
    deeper of two nested scopes wins. The runner uses this to confine a
    region's pass to the part of the region a subtree scan asked for.
    """
    if not outer or not inner or outer == inner:
        return outer or inner
    if inner.startswith(outer + "/"):
        return inner
    if outer.startswith(inner + "/"):
        return outer
    return None


def _on_scope_path(*, relative: str, scope: str) -> bool:
    """True if a directory is the scope, lies under it, or leads down to it."""
    return relative == scope or relative.startswith(scope + "/") or scope.startswith(relative + "/")


def set_excludes(patterns: tuple[str, ...] | list[str]) -> None:
    """Replace the exclude globs honoured by discovery for the current scan."""
    install_scan(replace(get_current_scan(), excludes=tuple(patterns)))


def get_active_excludes() -> tuple[str, ...]:
    """Return the exclude globs currently in effect."""
    return get_current_scan().excludes


def _is_excluded(*, relative: str, patterns: tuple[str, ...]) -> bool:
    """True if a forward-slashed relative path matches any exclude glob."""
    return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)


def _walk(root: Path, *, prune: frozenset[str]) -> Iterator[tuple[Path, str, list[str], list[str]]]:
    """Yield ``(directory, relative_prefix, dirnames, filenames)`` for each directory kept.

    *prune* names are dropped by basename; any directory whose root-relative
    path matches an active exclude glob is dropped too. Pruning happens in
    place so ``os.walk`` never descends a dropped subtree. The prefix is the
    directory's root-relative posix path plus ``/`` (empty at the root), so a
    file's relative path is one concatenation rather than a ``relative_to``.
    """
    scan = get_current_scan()
    patterns, scope = scan.excludes, scan.scope
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        prefix = "" if here == root else here.relative_to(root).as_posix() + "/"
        kept: list[str] = []
        for name in dirnames:
            if name in prune:
                continue
            if patterns and _is_excluded(relative=prefix + name, patterns=patterns):
                continue
            if scope and not _on_scope_path(relative=prefix + name, scope=scope):
                continue
            kept.append(name)
        dirnames[:] = sorted(kept)
        if scope and not prefix.startswith(scope + "/"):
            filenames = []  # a directory above the scope: only the path down counts
        yield here, prefix, dirnames, filenames


def iter_files(
    root: Path,
    *,
    suffix: str | None = None,
    prune: frozenset[str] = DEFAULT_PRUNE_DIRS,
) -> list[Path]:
    """Walk *root*, pruning default and excluded directories, sorted by path.

    Prunes *prune* (``DEFAULT_PRUNE_DIRS`` unless a check has its own vendor
    set) by basename and any directory whose root-relative path matches an
    active exclude glob. Files whose relative path matches an exclude glob are
    skipped too (so they are never read). If *suffix* is given, only files
    ending with it are returned.
    """
    patterns = get_current_scan().excludes
    found: list[Path] = []
    for here, prefix, _dirs, filenames in _walk(root, prune=prune):
        for name in filenames:
            if suffix is not None and not name.endswith(suffix):
                continue
            if patterns and _is_excluded(relative=prefix + name, patterns=patterns):
                continue
            found.append(here / name)
    return sorted(found)


def iter_dirs(root: Path, *, prune: frozenset[str] = DEFAULT_PRUNE_DIRS) -> list[Path]:
    """Every directory under *root* (the root excluded) that the walk keeps, sorted.

    Collected from each visited directory's kept children, so a symlink to a
    directory is listed even though the walk does not descend into it.
    """
    scope = get_current_scan().scope
    found: list[Path] = []
    for here, prefix, dirnames, _files in _walk(root, prune=prune):
        for name in dirnames:
            if scope and not (prefix + name).startswith(scope + "/") and prefix + name != scope:
                continue
            found.append(here / name)
    return sorted(found)


def iter_py_files(root: Path) -> list[Path]:
    """Walk *root* for ``*.py`` files, honouring pruning. Sorted by path."""
    return iter_files(root, suffix=".py")
