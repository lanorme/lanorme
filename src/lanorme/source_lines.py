"""Source lines of a project's files, read at most once per run.

Three stages after the checks look up a finding's source line: the inline
``# noqa`` / ``# lanorme: ignore`` filter, the baseline's content anchor, and
the fingerprint the JSON reports carry. They share one :class:`SourceLines`
so a file is read and decoded once per run rather than once per stage.
"""

from __future__ import annotations

from importlib.util import decode_source
from pathlib import Path


class SourceLines:
    """The lines of files under a project root, cached by project-relative path."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._files: dict[str, list[str]] = {}

    def read_line(self, *, file: str, line: int) -> str:
        """Line *line* (1-based) of *file*, or ``""`` when it cannot be read or is out of range."""
        lines = self._read_file(file)
        if not lines or line <= 0 or line > len(lines):
            return ""
        return lines[line - 1]

    def _read_file(self, file: str) -> list[str]:
        key = file.replace("\\", "/")
        lines = self._files.get(key)
        if lines is None:
            try:
                # Decoded the way the interpreter decodes a module (a BOM or a
                # ``coding:`` cookie is honoured), like ``lanorme.sources``, so
                # a directive in a latin-1 file is read rather than the whole
                # file dropped. ``decode_source`` raises SyntaxError on an
                # unknown cookie and UnicodeDecodeError (a ValueError) on bad
                # bytes.
                lines = decode_source((self.project_root / file).read_bytes()).splitlines()
            except (OSError, SyntaxError, ValueError):
                lines = []
            self._files[key] = lines
        return lines
