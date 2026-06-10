"""Tests for PATH-001 (forbidden_paths): configured forbidden directories.

The check is inert until configured, then fails once per forbidden directory
that exists in the tree. Vendor trees (.venv/ node_modules/ .git/ __pycache__/)
are ignored, only directories count (not like-named files), and matching is a
recursive rglob over directory names.

These tests drive the check directly (like test_strong_types.py) to sidestep the
lanorme.toml vs pyproject.toml config-table quirk.
"""

from __future__ import annotations

from pathlib import Path

from lanorme import Status
from lanorme.checks.forbidden_paths import ForbiddenPathsCheck


def _files(result) -> list[str]:
    return sorted(v.file for v in result.violations)


def test_inert_when_unconfigured(tmp_path: Path):
    # Arrange: a forbidden-looking directory exists, but the check is never
    # configured, so its default forbidden list is empty.
    (tmp_path / "build_artifacts").mkdir()

    # Act: run without any configure() call.
    result = ForbiddenPathsCheck().run(src_root=str(tmp_path))

    # Assert: inert means PASS with no findings (the opt-in guarantee).
    assert result.status == Status.PASS
    assert not result.violations


def test_empty_dirs_list_is_inert(tmp_path: Path):
    # Arrange: explicitly configured with an empty dirs list.
    (tmp_path / "build_artifacts").mkdir()
    check = ForbiddenPathsCheck()
    check.configure(settings={"dirs": []})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: an empty configuration is still inert.
    assert result.status == Status.PASS
    assert not result.violations


def test_configured_forbidden_dir_at_root_fails(tmp_path: Path):
    # Arrange: a configured forbidden directory sits at the project root.
    (tmp_path / "build_artifacts").mkdir()
    (tmp_path / "src").mkdir()
    check = ForbiddenPathsCheck()
    check.configure(settings={"dirs": ["build_artifacts", "legacy_src"]})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: a single PATH-001 failure naming the directory.
    assert result.status == Status.FAIL
    path001 = [v for v in result.violations if v.rule == "PATH-001"]
    assert len(path001) == 1
    assert path001[0].file == "build_artifacts"
    assert "build_artifacts" in path001[0].message
    assert path001[0].line == 0


def test_configured_but_absent_is_clean(tmp_path: Path):
    # Arrange: configured forbidden dirs, none of which exist in the tree.
    (tmp_path / "src").mkdir()
    check = ForbiddenPathsCheck()
    check.configure(settings={"dirs": ["build_artifacts"]})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: nothing to report.
    assert result.status == Status.PASS
    assert not result.violations


def test_file_named_like_token_does_not_fire(tmp_path: Path):
    # Arrange: a regular FILE (not a directory) whose name equals the token; the
    # is_dir guard must keep this silent.
    (tmp_path / "build_artifacts").write_text("contents", encoding="utf-8")
    check = ForbiddenPathsCheck()
    check.configure(settings={"dirs": ["build_artifacts"]})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: a like-named file is not a forbidden directory.
    assert result.status == Status.PASS
    assert not result.violations


def test_nested_forbidden_dir_is_found(tmp_path: Path):
    # Arrange: the forbidden directory is buried several levels deep.
    (tmp_path / "a" / "b" / "legacy_src").mkdir(parents=True)
    check = ForbiddenPathsCheck()
    check.configure(settings={"dirs": ["legacy_src"]})

    # Act: the recursive walk must reach it.
    result = check.run(src_root=str(tmp_path))

    # Assert: reported with its full relative path.
    assert result.status == Status.FAIL
    assert _files(result) == ["a/b/legacy_src"]


def test_vendor_trees_are_ignored(tmp_path: Path):
    # Arrange: a forbidden-named dir under each excluded vendor tree.
    (tmp_path / ".venv" / "build_artifacts").mkdir(parents=True)
    (tmp_path / "node_modules" / "build_artifacts").mkdir(parents=True)
    (tmp_path / "pkg" / "__pycache__" / "build_artifacts").mkdir(parents=True)
    (tmp_path / ".git" / "build_artifacts").mkdir(parents=True)
    check = ForbiddenPathsCheck()
    check.configure(settings={"dirs": ["build_artifacts"]})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the project does not own vendor trees, so none fire.
    assert result.status == Status.PASS
    assert not result.violations


def test_substring_lookalike_does_not_fire(tmp_path: Path):
    # Arrange: directories whose names merely CONTAIN the token as a substring;
    # rglob matches whole path components, not substrings.
    (tmp_path / "build_artifacts_old").mkdir()
    (tmp_path / "old_build_artifacts").mkdir()
    check = ForbiddenPathsCheck()
    check.configure(settings={"dirs": ["build_artifacts"]})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: no exact-name match, so no false positive.
    assert result.status == Status.PASS
    assert not result.violations


def test_vendor_prefix_lookalike_dir_is_not_excluded(tmp_path: Path):
    # Arrange: a directory named '.venvextra' is NOT a vendor tree; the vendor
    # fragment '.venv/' carries a trailing slash so it does not match a segment
    # for which it is only a prefix. The forbidden dir under it must still fire.
    (tmp_path / ".venvextra" / "legacy_src").mkdir(parents=True)
    check = ForbiddenPathsCheck()
    check.configure(settings={"dirs": ["legacy_src"]})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: not treated as vendor, so the forbidden dir is reported.
    assert result.status == Status.FAIL
    assert _files(result) == [".venvextra/legacy_src"]


def test_multiple_forbidden_dirs_each_reported(tmp_path: Path):
    # Arrange: two distinct forbidden directories both present.
    (tmp_path / "build_artifacts").mkdir()
    (tmp_path / "legacy_src" / "sub").mkdir(parents=True)
    check = ForbiddenPathsCheck()
    check.configure(settings={"dirs": ["build_artifacts", "legacy_src"]})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: one violation per forbidden directory.
    assert result.status == Status.FAIL
    assert _files(result) == ["build_artifacts", "legacy_src"]


def test_forbidding_a_vendor_name_is_a_noop(tmp_path: Path):
    # Arrange: you cannot forbid a directory whose name is itself a vendor token;
    # the vendor-exclusion rule swallows it. This locks the documented contract.
    (tmp_path / "node_modules").mkdir()
    check = ForbiddenPathsCheck()
    check.configure(settings={"dirs": ["node_modules"]})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: excluded as a vendor tree, so no violation.
    assert result.status == Status.PASS
    assert not result.violations


def test_forbidden_dir_under_dotgit_suffixed_project_dir_should_fire(tmp_path: Path):
    # Arrange: 'proj.git' is a legitimate project directory (e.g. a bare-repo
    # mirror), NOT the '.git' vendor tree. A forbidden dir lives inside it.
    (tmp_path / "proj.git" / "legacy_src").mkdir(parents=True)
    check = ForbiddenPathsCheck()
    check.configure(settings={"dirs": ["legacy_src"]})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert (correct behaviour): the forbidden dir is reported. Currently the
    # substring '.git/' match wrongly excludes it, so this xfails.
    assert result.status == Status.FAIL
    assert _files(result) == ["proj.git/legacy_src"]
