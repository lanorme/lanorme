"""The scan a run is making: where from, what it is confined to, and its parse cache.

A check receives the :class:`Scan` for its pass (``check(self, scan)``), yet
every walk must honour the run's exclude globs and subtree scope, and every
check should share one parse of each file, without a helper passing the scan
around. So :meth:`Scan.activate` makes a scan current for a block, through a
:class:`~contextvars.ContextVar`, and ``lanorme.discovery`` and
``lanorme.sources`` read the current one. The runner activates the scan it
hands each check; by hand::

    base = Scan(root=project_root, excludes=("vendor/*",))
    result = run_check(check, scan=base.restrict(scope="tests"))

A restricted scan shares its base's :class:`SourceCache`, so a file parsed in
one pass of a run is not parsed again in the next. With no scan active the
module default applies: the whole tree, no excludes, and a process-wide cache,
which is what a check run directly (outside the CLI) gets.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path

# Beyond this many cached modules the cache stops growing and further files
# are parsed per check. Bounds memory on a very large monorepo without
# changing any result.
_CACHE_LIMIT = 5000


class SourceCache:
    """Parse outcomes of one run, keyed by file path, up to a size limit.

    ``lanorme.sources`` decides what an entry is and when it is stale; this
    only stores it, so one cache can be shared by every pass of a run.
    """

    def __init__(self, *, limit: int = _CACHE_LIMIT) -> None:
        self._entries: dict[str, object] = {}
        self._limit = limit

    def get(self, key: str) -> object | None:
        """The entry stored under *key*, or ``None``."""
        return self._entries.get(key)

    def store(self, key: str, *, entry: object) -> None:
        """Keep *entry* under *key*, unless the cache is full."""
        if key in self._entries or len(self._entries) < self._limit:
            self._entries[key] = entry

    def clear(self) -> None:
        """Drop every entry."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


@dataclass(frozen=True)
class Scan:
    """One run's scan context: the tree, the confinement, and the shared parse cache.

    *root* is the directory the checks run from (the project root under the
    CLI) and *source_root* the project's top-level ``source_root`` setting,
    for a check that wants the run's layout. *scope* confines every walk to a
    root-relative posix directory (``""`` for the whole tree) and *excludes*
    are the globs the walk prunes, matched against root-relative paths.
    """

    root: Path = Path()
    scope: str = ""
    excludes: tuple[str, ...] = ()
    source_root: str = ""
    cache: SourceCache = field(default_factory=SourceCache, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", self.scope.strip("/"))
        object.__setattr__(self, "excludes", tuple(self.excludes))

    def restrict(self, *, scope: str | None = None, excludes: Iterable[str] = ()) -> Scan:
        """This scan confined to *scope* with *excludes* added, sharing its cache.

        *scope* replaces this scan's scope (the caller passes the narrower of
        the two); ``None`` keeps it.
        """
        return replace(
            self,
            scope=self.scope if scope is None else scope,
            excludes=(*self.excludes, *excludes),
        )

    @contextmanager
    def activate(self) -> Iterator[Scan]:
        """Make this the current scan for the ``with`` block, restoring the previous after."""
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)


_DEFAULT = Scan()
_current: ContextVar[Scan] = ContextVar("lanorme_scan", default=_DEFAULT)


def get_current_scan() -> Scan:
    """The scan in force: the innermost active one, else the whole-tree default."""
    return _current.get()


def install_scan(scan: Scan) -> None:
    """Make *scan* current until another replaces it, with no block to restore it.

    For the compatibility setters (``discovery.set_scope`` and friends);
    new code uses :meth:`Scan.activate`, which restores what came before.
    """
    _current.set(scan)
