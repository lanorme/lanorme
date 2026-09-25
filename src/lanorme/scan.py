"""The run context a check reads: where to walk, what to skip, what is cached.

A :class:`Scan` carries everything the runner knows about one pass over a
tree: the directory to walk, whether the pass covers the whole tree or one
config region's own files, the exclude globs in force, the project's
``source_root``, and a cache of sources and parsed modules so the checks in a
run read and parse each file once instead of once per check.

The runner activates the scan in a :class:`contextvars.ContextVar` for the
duration of a check's call, so helpers that still take a bare root
(:func:`lanorme.discovery.iter_files`) pick up the same excludes without being
handed the scan. A check written against the current protocol receives the
scan directly through ``check(scan)``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import discovery


class SourceCache:
    """Per-run memo of file contents and parsed modules, keyed by path.

    A miss reads or parses the file and stores the outcome; a read that fails
    is not cached, so a check sees the same exception it would have seen
    reading the file itself. Share one cache across the passes of a run and a
    file is read once for every check in it. The cached tree is the one every
    check sees, so a check that rewrites nodes works on a copy.
    """

    def __init__(self) -> None:
        self._text: dict[Path, str] = {}
        self._modules: dict[Path, ast.Module] = {}

    def text(self, path: Path) -> str:
        """The file's contents decoded as UTF-8, read once."""
        cached = self._text.get(path)
        if cached is None:
            cached = path.read_text(encoding="utf-8")
            self._text[path] = cached
        return cached

    def module(self, path: Path) -> ast.Module:
        """The file parsed once from bytes, so a BOM or coding cookie is honoured.

        Raises whatever the read or the parser raises (``OSError``,
        ``SyntaxError``, ``ValueError``, ``RecursionError``, ``MemoryError``).
        """
        cached = self._modules.get(path)
        if cached is None:
            cached = ast.parse(path.read_bytes(), filename=str(path))
            self._modules[path] = cached
        return cached


@dataclass
class Scan:
    """One pass over a tree: the root, its scope, excludes, and shared caches.

    ``root`` is the directory the pass walks; findings are reported relative
    to it. ``scope`` is ``"tree"`` for a pass over the whole scan root and
    ``"file"`` for a per-region pass that covers only the files that region
    directly governs (the vocabulary of the ``Check.scope`` attribute).
    ``excludes`` are the glob patterns discovery prunes during the walk, and
    ``source_root`` the project's configured architectural root, relative to
    the project root, or ``""`` when unset.
    """

    root: Path
    scope: str = "tree"
    excludes: tuple[str, ...] = ()
    source_root: str = ""
    cache: SourceCache = field(default_factory=SourceCache)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.excludes = tuple(self.excludes)

    @classmethod
    def for_root(cls, src_root: str | Path) -> Scan:
        """A scan of *src_root* under the excludes currently in force.

        This is what the deprecated ``run(*, src_root)`` entry point wraps: a
        caller that passes a bare path gets a scan whose excludes are those
        published to discovery for the run.
        """
        return cls(root=Path(src_root), excludes=discovery.active_excludes())

    def files(self, *, suffix: str | None = None) -> list[Path]:
        """Every file under the root, pruned and excluded, sorted by path."""
        return discovery.iter_files(self.root, suffix=suffix, excludes=self.excludes)

    def py_files(self) -> list[Path]:
        """Every ``*.py`` file under the root, pruned and excluded, sorted by path."""
        return self.files(suffix=".py")

    def relative(self, path: Path) -> str:
        """The root-relative, forward-slashed path a finding reports."""
        return path.relative_to(self.root).as_posix()

    def source(self, path: Path) -> str:
        """The file's UTF-8 text, read once per run."""
        return self.cache.text(path)

    def module(self, path: Path) -> ast.Module:
        """The file parsed once per run; raises what the read or parser raises."""
        return self.cache.module(path)

    def parsed_modules(self) -> Iterator[tuple[str, ast.Module]]:
        """Every parseable module under the root with its root-relative path.

        A file the parser rejects, including one that overflows it, is skipped
        rather than raised. A check that must report a parse error walks
        :meth:`py_files` and parses each file itself.
        """
        for path in self.py_files():
            try:
                tree = self.module(path)
            except (OSError, SyntaxError, ValueError, RecursionError, MemoryError):
                continue
            yield self.relative(path), tree


_current: ContextVar[Scan | None] = ContextVar("lanorme_scan", default=None)


def current_scan() -> Scan | None:
    """The scan the runner has activated for the check now running, if any."""
    return _current.get()


@contextmanager
def activate(scan: Scan) -> Iterator[Scan]:
    """Make *scan* the current scan for the duration of the block.

    Discovery's bare-root helpers read the scan's excludes for the same span,
    so a check still walking through ``iter_py_files(root)`` prunes what the
    scan prunes.
    """
    token = _current.set(scan)
    try:
        with discovery.scoped_excludes(scan.excludes):
            yield scan
    finally:
        _current.reset(token)
