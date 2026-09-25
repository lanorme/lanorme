"""Tests for the JUNK-001 / JUNK-002 stray-artifact check.

The regression of note: scratch directories written at the repo root (the
``.testdir`` / ``.pc_tmpdir`` shapes a test harness leaves behind) must be
flagged as JUNK-001, while legitimate extension-less dotfiles must not.
"""

from __future__ import annotations

from pathlib import Path

from lanorme import Status
from lanorme.checks.stray_artifacts import StrayArtifactsCheck
from lanorme.scan import Scan


def _collect_codes(tmp_path: Path) -> set[str]:
    result = StrayArtifactsCheck().check(Scan(root=tmp_path))
    return {(v.rule, v.file) for v in result.violations}


def test_scratch_temp_dir_files_are_flagged(tmp_path: Path):
    # Arrange: the exact scratch-file shapes a harness leaves at the root.
    for name in (".pc_tmpdir", ".testdir", ".testdir2", "build.tmpdir", "scratchdir.out"):
        (tmp_path / name).write_text("/tmp/whatever\n", encoding="utf-8")
    # Act
    flagged = {file for rule, file in _collect_codes(tmp_path) if rule == "JUNK-001"}
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
    result = StrayArtifactsCheck().check(Scan(root=tmp_path))
    assert result.status == Status.PASS
    assert not result.violations


def test_stray_image_at_root_is_flagged_but_asset_dir_image_is_not(tmp_path: Path):
    # Arrange: an identical image filename at the root and inside docs/, an
    # asset directory where images are expected.
    (tmp_path / "diagram.png").write_text("x\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "diagram.png").write_text("x\n", encoding="utf-8")
    # Act
    flagged = {file for rule, file in _collect_codes(tmp_path) if rule == "JUNK-002"}
    # Assert: only the stray root copy is flagged; the asset-dir copy is exempt.
    assert flagged == {"diagram.png"}


def test_allow_glob_exempts_a_stray_image(tmp_path: Path):
    # Arrange: a stray image that an allow entry whitelists by basename.
    (tmp_path / "diagram.png").write_text("x\n", encoding="utf-8")
    check = StrayArtifactsCheck()
    check.configure(settings={"allow": ["diagram.png"]})
    # Act
    result = check.check(Scan(root=tmp_path))
    # Assert: the allow entry suppresses the JUNK-002 finding.
    assert result.status == Status.PASS
    assert not result.violations


def test_core_module_is_not_a_core_dump(tmp_path: Path):
    # Arrange: a module and a docs page named core, beside a real core dump.
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "core.md").write_text("# core\n", encoding="utf-8")
    (tmp_path / "core.1234").write_bytes(b"\x7fELF")
    # Act
    flagged = {file for rule, file in _collect_codes(tmp_path) if rule == "JUNK-001"}
    # Assert
    assert flagged == {"core.1234"}


def test_parallel_mode_coverage_data_is_junk(tmp_path: Path):
    # Arrange: coverage's parallel-mode data file, named .coverage.<host>.<pid>.<rand>.
    (tmp_path / ".coverage.host.12.abc").write_bytes(b"SQLite")
    # Act
    flagged = {file for rule, file in _collect_codes(tmp_path) if rule == "JUNK-001"}
    # Assert
    assert flagged == {".coverage.host.12.abc"}


def test_fixture_and_resource_images_are_assets(tmp_path: Path):
    # Arrange: images a test suite and a package ship on purpose, and one stray.
    for rel in ("tests/fixtures/sample.png", "pkg/resources/icon.png", "pkg/icon.png"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG")
    # Act
    flagged = {file for rule, file in _collect_codes(tmp_path) if rule == "JUNK-002"}
    # Assert
    assert flagged == {"pkg/icon.png"}
