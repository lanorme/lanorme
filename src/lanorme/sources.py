"""Parsed Python sources, read and parsed once per run and shared by every check.

Two dozen checks walk the same tree and need the same thing from each file: its
text and its AST. Reading and parsing per check multiplied that work by the
number of checks; this module does it once and hands every check the same
:class:`Module`. It also fixes the parse policy in one place:

- Files are decoded the way the interpreter decodes them (a UTF-8 BOM and a
  ``coding:`` cookie are honoured), so a file that runs is a file that is
  scanned.
- A file the parser rejects, overflows on, or cannot read is reported as an
  :class:`Unparseable` rather than raised, so one pathological file can never
  blank a whole check. Each check decides whether to skip it silently or emit a
  ``<PREFIX>-000`` notice through :func:`skip_notice`.

Trees are shared, so a check must never mutate one; copy first.

The cache is process-global like the check registry and the active excludes,
because the ``Check.run(*, src_root)`` protocol carries no run context. The
CLI drops it at the start of each run (and the test suite around each test),
so a run never sees a tree from an earlier one. Within a process, an entry is
also checked against the file's size, inode and timestamps, which catches an
edit between two API calls except a same-size rewrite inside one timestamp
tick of a coarse-grained filesystem.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.util import decode_source
from pathlib import Path

from lanorme import Violation
from lanorme.discovery import iter_py_files

# Reasons a file yields an Unparseable. Rule strings built from them are stable
# public surface (``SIZE-000: parse error`` anchors a baseline entry).
PARSE_ERROR = "parse error"
TOO_DEEP = "too deeply nested"
UNREADABLE = "unreadable"

# Beyond this many cached modules the cache stops growing and further files
# are parsed per check, as before. Bounds memory on a very large monorepo
# without changing any result.
_CACHE_LIMIT = 5000


class NodeIndex:
    """Every node of a tree grouped by type, from one walk, built on first use.

    Most checks want "every function" or "every call" of a module; walking the
    whole tree for each of them multiplied the traversal by the number of
    checks. The index walks once and answers by type, in ``ast.walk`` order,
    so a check that switches from a walk to :meth:`nodes` reports the same
    findings in the same order.
    """

    def __init__(self, tree: ast.Module) -> None:
        self._tree = tree
        self._by_type: dict[type[ast.AST], list[ast.AST]] | None = None
        self._position: dict[int, int] = {}

    def _build(self) -> dict[type[ast.AST], list[ast.AST]]:
        if self._by_type is None:
            by_type: dict[type[ast.AST], list[ast.AST]] = {}
            for position, node in enumerate(ast.walk(self._tree)):
                by_type.setdefault(type(node), []).append(node)
                self._position[id(node)] = position
            self._by_type = by_type
        return self._by_type

    def nodes(self, *types: type[ast.AST]) -> list[ast.AST]:
        """The nodes whose exact type is one of *types*, in walk order."""
        by_type = self._build()
        if len(types) == 1:
            return list(by_type.get(types[0], ()))
        found = [node for wanted in types for node in by_type.get(wanted, ())]
        found.sort(key=lambda node: self._position[id(node)])
        return found

    @property
    def functions(self) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
        """Every function and method, nested ones included, in walk order."""
        return self.nodes(ast.FunctionDef, ast.AsyncFunctionDef)  # type: ignore[return-value]


@dataclass(frozen=True)
class Module:
    """One parsed source file: its path, root-relative posix path, text and tree."""

    path: Path
    relative: str
    source: str
    tree: ast.Module
    index: NodeIndex

    @property
    def lines(self) -> list[str]:
        """The source split into lines, for checks that report by line text."""
        return self.source.splitlines()


@dataclass(frozen=True)
class Unparseable:
    """A source file that could not be turned into a tree, and why."""

    path: Path
    relative: str
    reason: str


# What identifies one version of a file: size, inode, and both timestamps.
_Signature = tuple[int, int, int, int]


@dataclass(frozen=True)
class _Entry:
    """A cached parse outcome, tagged with the file signature it was read from."""

    signature: _Signature
    source: str
    tree: ast.Module | None
    reason: str
    index: NodeIndex | None = None


_cache: dict[str, _Entry] = {}


def clear_cache() -> None:
    """Drop every cached parse. The CLI calls this at the start of each run."""
    _cache.clear()


def _parse(path: Path, *, signature: _Signature) -> _Entry:
    """Read and parse *path*, mapping every failure to an :class:`Unparseable` reason."""
    try:
        raw = path.read_bytes()
    except OSError:
        return _Entry(signature=signature, source="", tree=None, reason=UNREADABLE)
    try:
        source = decode_source(raw)
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError):
        # ValueError covers a null byte and a UnicodeDecodeError alike.
        return _Entry(signature=signature, source="", tree=None, reason=PARSE_ERROR)
    except (RecursionError, MemoryError):
        return _Entry(signature=signature, source="", tree=None, reason=TOO_DEEP)
    return _Entry(signature=signature, source=source, tree=tree, reason="", index=NodeIndex(tree))


def _signature(path: Path) -> _Signature:
    """The cheap freshness key from one ``stat``; all zeros when the file is gone."""
    try:
        stat = path.stat()
    except OSError:
        return (0, 0, 0, 0)
    return (stat.st_size, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns)


def _entry_for(path: Path) -> _Entry:
    """The parse outcome for *path*, from the cache when it is still current."""
    key = str(path)
    signature = _signature(path)
    cached = _cache.get(key)
    if cached is not None and cached.signature == signature:
        return cached
    entry = _parse(path, signature=signature)
    if entry.tree is not None and len(_cache) < _CACHE_LIMIT:
        _cache[key] = entry
    return entry


def parse_module(path: Path, *, root: Path) -> Module | Unparseable:
    """Parse one file, reporting it relative to *root*."""
    relative = path.relative_to(root).as_posix()
    entry = _entry_for(path)
    if entry.tree is None or entry.index is None:
        return Unparseable(path=path, relative=relative, reason=entry.reason)
    return Module(
        path=path, relative=relative, source=entry.source, tree=entry.tree, index=entry.index
    )


def iter_modules(root: Path) -> Iterator[Module | Unparseable]:
    """Every ``.py`` file under *root* (pruned like :func:`iter_py_files`), parsed.

    Yields an :class:`Unparseable` for a file the parser rejects so the check can
    choose its policy; use :func:`parsed_modules` to skip those silently.
    """
    root = Path(root)
    for path in iter_py_files(root):
        yield parse_module(path, root=root)


def parsed_modules(root: Path) -> Iterator[Module]:
    """Every parseable module under *root*; files that fail to parse are skipped."""
    for module in iter_modules(root):
        if isinstance(module, Module):
            yield module


def span(node: ast.AST) -> dict[str, int | None]:
    """The ``column`` / ``end_line`` / ``end_column`` keywords for a finding at *node*.

    Spread into ``Violation(...)`` so a consumer can place an edit inside the
    line and know a definition's extent. Nodes without positions give ``None``.
    """
    return {
        "column": getattr(node, "col_offset", None),
        "end_line": getattr(node, "end_lineno", None),
        "end_column": getattr(node, "end_col_offset", None),
    }


def skip_notice(*, prefix: str, file: str, name: str, reason: str) -> Violation:
    """The advisory ``<PREFIX>-000`` notice a check emits when it skips a file.

    A ``-000`` code is a notice, not a finding: promotion never escalates it and
    the baseline never records it as debt. *reason* is one of the module
    constants; *name* is the file's basename for the message.
    """
    if reason == TOO_DEEP:
        message = f"{name} is too deeply nested to analyse — skipping"
        fix = f"No action needed; this file is exempt from the {prefix} rules"
    elif reason == UNREADABLE:
        message = f"Could not read {name} — skipping"
        fix = "Check the file's permissions"
    else:
        message = f"Could not parse {name} — skipping"
        fix = "Fix the syntax error first"
    return Violation(file=file, line=0, rule=f"{prefix}-000: {reason}", message=message, fix=fix)


def unparseable_notice(*, prefix: str, failure: Unparseable) -> Violation:
    """:func:`skip_notice` for a file :func:`iter_modules` could not parse."""
    return skip_notice(
        prefix=prefix, file=failure.relative, name=failure.path.name, reason=failure.reason
    )


def cache_size() -> int:
    """Number of parsed modules currently held; for tests and diagnostics."""
    return len(_cache)


__all__ = [
    "Module",
    "NodeIndex",
    "Unparseable",
    "PARSE_ERROR",
    "TOO_DEEP",
    "UNREADABLE",
    "clear_cache",
    "cache_size",
    "iter_modules",
    "parse_module",
    "parsed_modules",
    "skip_notice",
    "span",
    "unparseable_notice",
]