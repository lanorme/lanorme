"""JUNK-001 / JUNK-002: Stray artifact detection.

Flags files that tend to accumulate as clutter, screenshots, scratch scripts,
editor backups, OS junk, build leftovers, and stray images/binaries dropped
outside an asset directory. The goal is to surface them in a normal
``lanorme check`` run so they get deleted (or deliberately allowlisted) rather
than quietly polluting the tree, whether or not they are tracked by git.

Rules:
    JUNK-001  A file matches a scratch/temp/OS/build junk pattern
             (``screenshot*``, ``*~``, ``*.bak``, ``.DS_Store``, ``*.pyc`` …).
    JUNK-002  An image/binary file sits outside any asset directory
             (``docs/``, ``assets/``, ``static/`` …).

This check is on by default with conservative patterns. Tune it in
``[tool.lanorme.stray_artifacts]``::

    [tool.lanorme.stray_artifacts]
    patterns   = ["*.heic"]            # extra name globs flagged as JUNK-001
    extensions = [".zip", ".pdf"]      # extra extensions flagged as JUNK-002
    assets     = ["screenshots"]       # extra dirs where binaries are allowed
    allow      = ["docs/diagram.png"]  # globs (path or name) never flagged
    exclude    = ["sandbox"]           # extra directories to skip entirely

Run:
    lanorme check . --check=stray_artifacts
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import read_str_list
from lanorme.discovery import iter_files

# Directories never scanned (vendored / generated / VCS).
_VENDOR_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        ".eggs",
        "dist",
        "build",
    },
)

# Directories where images/binaries are expected and therefore not JUNK-002.
# ``fixtures`` and ``resources`` hold the binaries tests and packages ship on
# purpose (``tests/fixtures/sample.png``, ``pkg/resources/icon.png``).
_DEFAULT_ASSET_DIRS = (
    "assets",
    "static",
    "images",
    "img",
    "media",
    "public",
    "docs",
    ".github",
    "fixtures",
    "resources",
)

# Name globs that are almost always clutter (JUNK-001), matched on the basename.
_DEFAULT_NAME_GLOBS = (
    "screenshot*",
    "Screenshot*",
    "Screen Shot*",
    "screen shot*",
    "*~",
    "*.bak",
    "*.orig",
    "*.rej",
    "*.swp",
    "*.swo",
    "*.tmp",
    "*tmpdir*",
    "*tempdir*",
    "*testdir*",
    "*scratchdir*",
    "scratch*",
    "Scratch*",
    "untitled*",
    "Untitled*",
    "tmp.*",
    "temp.*",
    "*.pyc",
    "*.pyo",
    ".coverage",
    ".coverage.*",
    "coverage.xml",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "nohup.out",
    # A core dump is ``core`` plus a pid; ``core.py`` is a module.
    "core.[0-9]*",
)

# Image/binary extensions flagged (JUNK-002) when outside an asset directory.
_DEFAULT_BINARY_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def _matches_any(*, name: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, glob) for glob in globs)


@dataclass
class StrayArtifactsCheck:
    """Flags stray clutter files (screenshots, scratch, OS junk, stray binaries)."""

    settings_keys: ClassVar[frozenset[str]] = frozenset(
        {"patterns", "extensions", "assets", "allow", "exclude"},
    )

    name: str = "stray_artifacts"
    description: str = "Stray artifact detection (screenshots, scratch files, OS junk)"
    extra_patterns: tuple[str, ...] = ()
    extra_extensions: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    extra_excludes: tuple[str, ...] = ()
    asset_dirs: tuple[str, ...] = _DEFAULT_ASSET_DIRS
    rules: list[str] = field(
        default_factory=lambda: [
            "JUNK-001: Scratch/temp/screenshot/OS/build artifacts must not pollute the tree",
            "JUNK-002: Images/binaries outside an asset directory are flagged as stray",
        ],
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.stray_artifacts]`` configuration."""
        self.extra_patterns = read_str_list(settings=settings, key="patterns")
        self.extra_extensions = tuple(
            e.lower() for e in read_str_list(settings=settings, key="extensions")
        )
        self.allow = read_str_list(settings=settings, key="allow")
        self.extra_excludes = read_str_list(settings=settings, key="exclude")
        if "assets" in settings:
            self.asset_dirs = (
                *_DEFAULT_ASSET_DIRS,
                *read_str_list(settings=settings, key="assets"),
            )

    def _classify(self, *, rel: Path) -> str | None:
        """Return the rule code a file violates, or None if it is fine."""
        rel_posix = rel.as_posix()
        if any(
            fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch(rel.name, pat) for pat in self.allow
        ):
            return None

        if _matches_any(name=rel.name, globs=_DEFAULT_NAME_GLOBS + self.extra_patterns):
            return "JUNK-001"

        if any(part in self.asset_dirs for part in rel.parts[:-1]):
            return None

        if rel.suffix.lower() in (*_DEFAULT_BINARY_EXTS, *self.extra_extensions):
            return "JUNK-002"

        return None

    def run(self, *, src_root: str) -> CheckResult:
        violations: list[Violation] = []
        root = Path(src_root)
        skip = _VENDOR_DIRS | frozenset(self.extra_excludes)

        for path in iter_files(root, prune=skip):
            rel = path.relative_to(root)
            code = self._classify(rel=rel)
            if code is None:
                continue
            violations.append(_build_violation(code=code, rel=rel))

        return CheckResult.from_findings(check=self.name, violations=violations)


def _build_violation(*, code: str, rel: Path) -> Violation:
    if code == "JUNK-001":
        message = f"Stray artifact '{rel.as_posix()}' matches a scratch/temp/OS-junk pattern"
    else:
        message = f"Stray image/binary '{rel.as_posix()}' sits outside an asset directory"
    return Violation(
        file=rel.as_posix(),
        line=0,
        rule=code,
        message=message,
        fix=(
            "Delete the file; if it is intentional, list it under 'allow' (or its "
            "directory under 'assets' or 'exclude') in [tool.lanorme.stray_artifacts]"
        ),
    )


register(StrayArtifactsCheck())
