"""PATH-001: Project-level structural invariants, forbidden directories.

Some directories should simply never exist in a project (build artefacts,
leftover scaffolding, directories that belong to a different layout). This
check fails if any configured directory name appears anywhere in the tree.

Configure the forbidden list in ``[tool.lanorme.forbidden_paths]``::

    [tool.lanorme.forbidden_paths]
    dirs = ["build_artifacts", "legacy_src"]

With no configuration the check is inert (always PASS), so it never produces
false positives on a project that has not opted in.

Directories named one of the forbidden tokens but living inside a vendor tree
(``.venv/``, ``node_modules/``, ``.git/``, ``__pycache__/``) are ignored, the
project does not own those.

Run:
    lanorme check . --check=forbidden_paths
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import read_str_list
from lanorme.discovery import iter_dirs

# Default is empty → the check is inert until configured.
_FORBIDDEN_DIRS: tuple[str, ...] = ()
_VENDOR_SEGMENTS = frozenset(
    {
        ".venv",
        "node_modules",
        ".git",
        "__pycache__",
    },
)


def _is_forbidden(*, relative: str, pattern: str) -> bool:
    """True if *relative* names a directory *pattern* forbids, at any depth.

    A bare name (``build_artifacts``, ``tmp*``) matches a directory's own name
    anywhere in the tree, never its descendants; a path (``legacy/src``)
    matches wherever those segments end a path. Globs are allowed in either.
    """
    if "/" not in pattern:
        return fnmatch.fnmatch(relative.rpartition("/")[2], pattern)
    return fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(relative, f"*/{pattern}")


@dataclass
class ForbiddenPathsCheck:
    """Asserts that configured forbidden directories do not exist in the tree."""

    settings_keys: ClassVar[frozenset[str]] = frozenset({"dirs"})

    name: str = "forbidden_paths"
    description: str = "Project-level invariants: forbidden directories must not exist"
    forbidden_dirs: tuple[str, ...] = _FORBIDDEN_DIRS
    rules: list[str] = field(
        default_factory=lambda: [
            "PATH-001: Configured forbidden directories must not exist",
        ],
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.forbidden_paths]`` configuration."""
        self.forbidden_dirs = read_str_list(settings=settings, key="dirs")

    def run(self, *, src_root: str) -> CheckResult:
        violations: list[Violation] = []
        root = Path(src_root)
        if not self.forbidden_dirs:
            return CheckResult.from_findings(check=self.name)

        # Vendor trees are pruned during the walk, so a forbidden name inside
        # one is never seen; the user's excludes prune it the same way.
        directories = [
            d.relative_to(root).as_posix() for d in iter_dirs(root, prune=_VENDOR_SEGMENTS)
        ]
        for forbidden in self.forbidden_dirs:
            for relative in directories:
                if not _is_forbidden(relative=relative, pattern=forbidden):
                    continue
                violations.append(
                    Violation(
                        file=relative,
                        line=0,
                        rule="PATH-001",
                        message=f"Forbidden directory '{relative}' exists",
                        fix=f"Delete '{relative}' or remove it from the forbidden list",
                    ),
                )

        return CheckResult.from_findings(check=self.name, violations=violations)


register(ForbiddenPathsCheck())
