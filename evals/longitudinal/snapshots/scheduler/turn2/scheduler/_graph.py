"""Dependency graph helpers: cycle detection over the task graph.

The graph is expressed as ``deps_of[name]`` -> the names that ``name``
depends on (must finish before ``name`` may start). Cycle detection walks
this graph with an iterative depth-first search so it works on graphs deeper
than the interpreter's default recursion limit.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

_WHITE = 0  # not yet visited
_GRAY = 1  # on the current DFS path
_BLACK = 2  # fully explored, known cycle-free


def find_cycle(
    names: Iterable[str], deps_of: Mapping[str, Iterable[str]]
) -> list[str] | None:
    """Return a cycle as a list of names if one exists, else ``None``.

    The returned list starts and ends on the same task name, and each name
    depends on the next one, e.g. ``["a", "b", "c", "a"]`` reads as "a
    depends on b depends on c depends on a".
    """
    color: dict[str, int] = {name: _WHITE for name in names}

    for start in color:
        if color[start] != _WHITE:
            continue
        cycle = _walk_from(start, color, deps_of)
        if cycle is not None:
            return cycle
    return None


def _walk_from(
    start: str, color: dict[str, int], deps_of: Mapping[str, Iterable[str]]
) -> list[str] | None:
    """Iterative DFS from ``start``, mutating ``color`` as it goes."""
    color[start] = _GRAY
    path = [start]
    stack: list[tuple[str, Iterable[str]]] = [(start, iter(deps_of.get(start, ())))]

    while stack:
        node, dep_iter = stack[-1]
        for dep in dep_iter:
            state = color.get(dep, _WHITE)
            if state == _WHITE:
                color[dep] = _GRAY
                path.append(dep)
                stack.append((dep, iter(deps_of.get(dep, ()))))
                break
            if state == _GRAY:
                idx = path.index(dep)
                return [*path[idx:], dep]
            # _BLACK: already fully explored, no cycle down that path.
        else:
            color[node] = _BLACK
            stack.pop()
            path.pop()

    return None
