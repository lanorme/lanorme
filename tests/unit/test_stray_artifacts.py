"""Tests for the JUNK-001 / JUNK-002 stray-artifact check.

The regression of note: scratch directories written at the repo root (the
``.testdir`` / ``.pc_tmpdir`` shapes a test harness leaves behind) must be
flagged as JUNK-001, while legitimate extension-less dotfiles must not.
"""

from __future__ import annotations

from pathlib import Path

from lanorme import Status
from lanorme.checks.stray_artifacts import StrayArtifactsCheck


def _codes(tmp_path: Path) -> set[str]:
    result = StrayArtifactsCheck().run(src_root=str(tmp_path))
    return {(v.rule, v.file) for v in result.violations}


def test_scratch_temp_dir_files_are_flagged(tmp_path: Path):
    # Arrange: the exact scratch-file shapes a harness leaves at the root.
    for name in (".pc_tmpdir", ".testdir", ".testdir2", "build.tmpdir", "scratchdir.out"):
        (tmp_path / name).write_text("/tmp/whatever\n", encoding="utf-8")
    # Act
    flagged = {file for rule, file in _codes(tmp_path) if rule == "JUNK-001"}
    # Assert
    assert flagged == {".pc_tmpdir", ".testdir", ".testdir2", "build.tmpdir", "scratchdir.out"}


def test_legitimate_dotfiles_are_not_flagged(tmp_path: Path):
    # Arrange: real config dotfiles that share the extension-less shape.
    for name in (
        ".gitignore",
        ".editorconfig",
        ".python-version",
        ".gitattributes",
        ".dockerignore",
        ".pre-commit-config.yaml",
    ):
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    # Act + Assert: nothing flagged.
    result = StrayArtifactsCheck().run(src_root=str(tmp_path))
    assert result.status == Status.PASS
    assert not result.violations
