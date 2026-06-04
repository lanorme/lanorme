"""Unit tests for the cascading per-directory config model (issue #28).

These cover the pure region resolution: merge precedence, inheritance, the
``root = true`` stop, nested-region pruning globs, and the pristine snapshot the
runner uses to keep one region's config from leaking into the next.
"""

from __future__ import annotations

from pathlib import Path

from lanorme.regions import (
    Region,
    child_exclude_globs,
    discover_regions,
    merge_config,
    restore_defaults,
    snapshot_defaults,
)


def test_merge_override_wins_and_subtables_merge():
    """Override replaces scalars but merges nested tables key by key."""
    # Arrange
    base = {"select": ["A"], "similarity": {"enabled": False, "min_statements": 5}}
    override = {"similarity": {"enabled": True}}

    # Act
    merged = merge_config(base=base, override=override)

    # Assert
    assert merged["select"] == ["A"]
    assert merged["similarity"] == {"enabled": True, "min_statements": 5}


def test_single_region_when_no_nested_config(tmp_path: Path):
    """A tree without nested config resolves to exactly one region (hot path)."""
    # Arrange
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")

    # Act
    regions = discover_regions(scan_root=tmp_path, root_config={"select": ["A"]})

    # Assert
    assert len(regions) == 1
    assert regions[0].directory == tmp_path.resolve()
    assert regions[0].merged == {"select": ["A"]}


def test_empty_config_file_creates_no_region(tmp_path: Path):
    """An empty config file is a no-op: it adds no region (output is unchanged)."""
    # Arrange: a nested config file with no keys, which would inherit the parent
    # wholesale, so creating a region for it changes nothing.
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "lanorme.toml").write_text("", encoding="utf-8")

    # Act
    regions = discover_regions(scan_root=tmp_path, root_config={"select": ["A"]})

    # Assert
    assert len(regions) == 1


def test_nested_region_inherits_then_overrides(tmp_path: Path):
    """A nested region inherits parent keys and overrides only what it sets."""
    # Arrange
    strict = tmp_path / "strict"
    strict.mkdir()
    (strict / "lanorme.toml").write_text('select = ["B"]\n', encoding="utf-8")

    # Act
    regions = discover_regions(
        scan_root=tmp_path,
        root_config={"similarity": {"enabled": True}, "select": ["A"]},
    )
    nested = _region_at(regions, strict)

    # Assert: 'select' is overridden; 'similarity' is inherited untouched.
    assert nested.merged == {"similarity": {"enabled": True}, "select": ["B"]}


def test_root_true_stops_inheritance(tmp_path: Path):
    """``root = true`` makes a nested region ignore its ancestors."""
    # Arrange
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    (standalone / "lanorme.toml").write_text("root = true\n", encoding="utf-8")

    # Act
    regions = discover_regions(
        scan_root=tmp_path,
        root_config={"similarity": {"enabled": True}},
    )
    nested = _region_at(regions, standalone)

    # Assert: the parent's similarity setting does not leak past the root
    # barrier, and the cascade-control key never reaches the merged config.
    assert nested.merged == {}


def test_child_exclude_globs_prune_nested_regions(tmp_path: Path):
    """A region's globs prune the directory and the whole subtree of each child."""
    # Arrange
    parent = Region(directory=tmp_path, raw={})
    child = Region(directory=tmp_path / "a" / "b", raw={})

    # Act
    globs = child_exclude_globs(region=parent, regions=[parent, child])

    # Assert
    assert globs == ["a/b", "a/b/*"]


def test_snapshot_restore_resets_mutated_state():
    """Restoring the snapshot returns a configured check to its defaults."""

    class _Toggle:
        def __init__(self):
            self.enabled = False

    # Arrange
    check = _Toggle()
    snapshot = snapshot_defaults({"toggle": check})

    # Act
    check.enabled = True
    restore_defaults(checks={"toggle": check}, snapshot=snapshot)

    # Assert
    assert check.enabled is False


def _region_at(regions: list[Region], directory: Path) -> Region:
    """Return the resolved region whose directory matches *directory*."""
    target = directory.resolve()
    return next(region for region in regions if region.directory == target)
