"""End-to-end tests for cascading per-directory config (issue #28).

Each test builds a small tree with a nested ``lanorme.toml``, runs the full
``check`` command (cascading only applies to a full run, not a single ``--check``
selector), and asserts which files a check fires on. ``naming_consistency`` is a
clean lever: its NAMING-001 repository rule is opt-in via ``repo_crud`` and fires
on any method under ``infrastructure/repositories/`` whose name uses a synonym
prefix, so toggling it per region is directly observable.
"""

from __future__ import annotations

import json
from pathlib import Path

from lanorme.cli import main

_REPO_METHOD = "class Repo:\n    def fetch_thing(self):\n        return 1\n"

_DUP_FUNCTION = (
    "def compute():\n"
    "    a = 1\n"
    "    b = 2\n"
    "    c = a + b\n"
    "    d = c * 2\n"
    "    return d\n"
)


def _repo_file(directory: Path) -> None:
    """Write a repository module with a synonym-prefixed method (NAMING-001 bait)."""
    repo = directory / "infrastructure" / "repositories"
    repo.mkdir(parents=True)
    (repo / "store.py").write_text(_REPO_METHOD, encoding="utf-8")


def _root_and_strict_repo(tmp_path: Path, strict_config: str) -> None:
    """Build a root region (no config) and a nested ``strict`` region, each with a repo."""
    (tmp_path / "lanorme.toml").write_text("", encoding="utf-8")
    _repo_file(tmp_path)
    strict = tmp_path / "strict"
    strict.mkdir()
    (strict / "lanorme.toml").write_text(strict_config, encoding="utf-8")
    _repo_file(strict)


def _run_full(root: Path, capsys, *extra: str) -> dict:
    """Run ``check <root> --json`` over the whole tree, returning results by name."""
    try:
        main(["check", str(root), "--json", *extra])
    except SystemExit:
        pass
    payload = json.loads(capsys.readouterr().out)
    return {result["check"]: result for result in payload}


def _violation_files(result: dict) -> set[str]:
    """The set of file paths a check reported violations on."""
    return {violation["file"] for violation in result["violations"]}


def test_nested_config_enables_check_only_in_its_subtree(tmp_path: Path, capsys):
    """repo_crud set only in the nested region fires NAMING-001 only under it."""
    # Arrange
    _root_and_strict_repo(tmp_path, "[naming_consistency]\nrepo_crud = true\n")

    # Act
    results = _run_full(tmp_path, capsys)

    # Assert
    assert _violation_files(results["naming_consistency"]) == {
        "strict/infrastructure/repositories/store.py"
    }


def test_nested_config_inherits_parent_setting(tmp_path: Path, capsys):
    """A nested region that sets only service_crud still inherits repo_crud."""
    # Arrange
    (tmp_path / "lanorme.toml").write_text(
        "[naming_consistency]\nrepo_crud = true\n", encoding="utf-8"
    )
    _repo_file(tmp_path)
    strict = tmp_path / "strict"
    strict.mkdir()
    (strict / "lanorme.toml").write_text(
        "[naming_consistency]\nservice_crud = true\n", encoding="utf-8"
    )
    _repo_file(strict)

    # Act
    results = _run_full(tmp_path, capsys)

    # Assert
    assert _violation_files(results["naming_consistency"]) == {
        "infrastructure/repositories/store.py",
        "strict/infrastructure/repositories/store.py",
    }


def test_root_true_stops_inheritance(tmp_path: Path, capsys):
    """``root = true`` in the nested region drops the inherited repo_crud."""
    # Arrange
    (tmp_path / "lanorme.toml").write_text(
        "[naming_consistency]\nrepo_crud = true\n", encoding="utf-8"
    )
    _repo_file(tmp_path)
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    (standalone / "lanorme.toml").write_text("root = true\n", encoding="utf-8")
    _repo_file(standalone)

    # Act
    results = _run_full(tmp_path, capsys)

    # Assert
    assert _violation_files(results["naming_consistency"]) == {
        "infrastructure/repositories/store.py"
    }


def test_whole_tree_check_spans_regions(tmp_path: Path, capsys):
    """A duplicate pair split across regions is still caught (DRY runs at root)."""
    # Arrange
    (tmp_path / "lanorme.toml").write_text("", encoding="utf-8")
    (tmp_path / "first.py").write_text(_DUP_FUNCTION, encoding="utf-8")
    strict = tmp_path / "strict"
    strict.mkdir()
    (strict / "lanorme.toml").write_text(
        "[similarity]\nenabled = false\n", encoding="utf-8"
    )
    (strict / "second.py").write_text(_DUP_FUNCTION, encoding="utf-8")

    # Act
    results = _run_full(tmp_path, capsys)
    reported = _violation_files(results["duplication"])

    # Assert
    assert "first.py" in reported
    assert "strict/second.py" in reported


def test_user_exclude_drops_nested_region_findings(tmp_path: Path, capsys):
    """A user --exclude over a nested region still drops that region's findings."""
    # Arrange
    (tmp_path / "lanorme.toml").write_text(
        "[naming_consistency]\nrepo_crud = true\n", encoding="utf-8"
    )
    _repo_file(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "lanorme.toml").write_text("[similarity]\nenabled = false\n", encoding="utf-8")
    _repo_file(sub)

    # Act
    results = _run_full(tmp_path, capsys, "--exclude", "sub/*")

    # Assert: only the root region's finding survives the exclude.
    assert _violation_files(results["naming_consistency"]) == {
        "infrastructure/repositories/store.py"
    }


def test_config_does_not_leak_between_invocations(tmp_path: Path, capsys):
    """A check configured in one run resets before the next run in the same process."""
    # Arrange: a first tree that enables repo_crud, a second with no config.
    enabled = tmp_path / "enabled"
    enabled.mkdir()
    (enabled / "lanorme.toml").write_text(
        "[naming_consistency]\nrepo_crud = true\n", encoding="utf-8"
    )
    _repo_file(enabled)
    plain = tmp_path / "plain"
    plain.mkdir()
    _repo_file(plain)

    # Act: two sequential invocations in one process.
    first = _run_full(enabled, capsys)
    second = _run_full(plain, capsys)

    # Assert: the first fires NAMING-001; the second does not inherit it.
    assert _violation_files(first["naming_consistency"])
    assert _violation_files(second["naming_consistency"]) == set()


def test_single_check_selector_uses_root_config_not_regions(tmp_path: Path, capsys):
    """A ``--check NAME`` run bypasses cascading and uses the root config only."""
    # Arrange: repo_crud is enabled only in the nested region, never at the root.
    _root_and_strict_repo(tmp_path, "[naming_consistency]\nrepo_crud = true\n")

    # Act: a single-check run does not apply the nested region's config.
    results = _run_full(tmp_path, capsys, "--check", "naming_consistency")

    # Assert: the nested repo_crud is not honoured under the bypass.
    assert _violation_files(results["naming_consistency"]) == set()
