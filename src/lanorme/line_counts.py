"""Line measures shared by the rules that size a file.

SIZE-001 counts a file's effective lines: lines that are neither blank nor a
comment, docstrings included. SHALLOW-001 sizes a merged module with the same
count, so the two rules can never measure a file differently, and it judges how
much code a module carries with :func:`count_code_lines`, which also leaves
docstring lines out.
"""

from __future__ import annotations

from lanorme.sources import Module


def is_effective_line(*, line: str) -> bool:
    """True for a line that is neither blank nor a comment."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def count_effective_lines(*, source: str) -> int:
    """SIZE-001's measure: non-blank, non-comment lines, docstrings included."""
    return sum(1 for line in source.splitlines() if is_effective_line(line=line))


def count_code_lines(*, module: Module) -> int:
    """Effective lines outside every module, class and function docstring.

    A docstring's whole span is left out, the line it starts on included, so a
    one-line docstring sharing its ``class`` line takes that line with it.
    """
    docstring_lines: set[int] = set()
    for docstring in module.docstrings:
        node = docstring.node
        docstring_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return sum(
        1
        for number, line in enumerate(module.lines, start=1)
        if number not in docstring_lines and is_effective_line(line=line)
    )
