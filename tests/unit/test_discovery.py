"""Tests for the shared file-discovery helper (traversal pruning + excludes)."""

from __future__ import annotations

from pathlib import Path

from lanorme import discovery
from lanorme.discovery import iter_py_files, set_excludes


def _make_tree(root: Path) -> None:
    # Arrange: a tree mixing real source with junk and a build dir.
    for rel in ("pkg/a.py", "keep/b.py", ".venv/lib/junk.py", "build/x.py", "pkg/sub/c.py"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")


def test_default_dirs_are_pruned(tmp_path: Path):
    # Arrange.
    _make_tree(tmp_path)

    # Act.
    found = {p.relative_to(tmp_path).as_posix() for p in iter_py_files(tmp_path)}

    # Assert: .venv and build are never traversed; real source is returned.
    assert found == {"pkg/a.py", "pkg/sub/c.py", "keep/b.py"}


def test_exclude_glob_prunes_directory(tmp_path: Path):
    # Arrange.
    _make_tree(tmp_path)
    set_excludes(("keep/*",))

    # Act.
    found = {p.relative_to(tmp_path).as_posix() for p in iter_py_files(tmp_path)}

    # Assert: the excluded subtree is gone, the rest stays.
    assert found == {"pkg/a.py", "pkg/sub/c.py"}


def test_excludes_do_not_leak_after_reset(tmp_path: Path):
    # Arrange: a run sets an exclude, then the autouse fixture resets it.
    _make_tree(tmp_path)
    set_excludes(("pkg/**",))
    discovery.set_excludes(())  # what the autouse reset does between tests

    # Act.
    found = {p.relative_to(tmp_path).as_posix() for p in iter_py_files(tmp_path)}

    # Assert: with no active excludes, pkg/ is visible again.
    assert "pkg/a.py" in found


# --------------------------------------------------------------------------- #
# narrowing one scope by another
# --------------------------------------------------------------------------- #


def test_find_narrower_scope_keeps_the_deeper_of_two_nested_scopes():
    # Arrange: the whole tree paired with a subtree, equal scopes, and nesting both ways.
    pairs = [("", ""), ("tests", ""), ("", "tests"), ("tests", "tests")]
    nested = [("tests", "tests/unit"), ("tests/unit", "tests")]

    # Act.
    narrowed = [discovery.find_narrower_scope(outer=o, inner=i) for o, i in pairs + nested]

    # Assert: the whole tree defers to the other scope, nesting picks the deeper.
    assert narrowed == ["", "tests", "tests", "tests", "tests/unit", "tests/unit"]


def test_find_narrower_scope_is_none_for_disjoint_scopes():
    # Arrange / Act / Assert: a sibling, and a name that is only a prefix, are disjoint.
    assert discovery.find_narrower_scope(outer="tests", inner="src") is None
    assert discovery.find_narrower_scope(outer="tests", inner="tests_extra") is None
