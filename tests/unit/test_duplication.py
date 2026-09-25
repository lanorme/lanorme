"""Tests for DRY-001 exact structural clone detection.

The regression of note: a single deeply nested file overflowed the ``deepcopy``
used to normalise a function body and crashed the whole run. One bad file must be
skipped, not fatal, and the rest of the tree must still be checked.
"""

from __future__ import annotations

from pathlib import Path

from lanorme import Status
from lanorme.checks.duplication import DuplicationCheck

_DUP_BODY = (
    "def {name}(a, b):\n"
    "    total = 0\n"
    "    total = a + b\n"
    "    total = total + 1\n"
    "    total = total - 0\n"
    "    return total\n"
)


def test_deeply_nested_file_is_skipped_not_crashed(tmp_path: Path):
    # Arrange: a file that parses but overflows deepcopy during normalisation
    # (depth 250: above the deepcopy limit, below the parser limit), beside a
    # genuine duplicate pair.
    (tmp_path / "deep.py").write_text(
        "def f():\n    return " + "[" * 250 + "]" * 250 + "\n",
        encoding="utf-8",
    )
    (tmp_path / "a.py").write_text(_DUP_BODY.format(name="alpha"), encoding="utf-8")
    (tmp_path / "b.py").write_text(_DUP_BODY.format(name="beta"), encoding="utf-8")

    # Act: the run must complete rather than raise RecursionError.
    result = DuplicationCheck().run(src_root=str(tmp_path))

    # Assert: the deep file is skipped with a DRY-000 warning, and the genuine
    # duplicate is still detected.
    assert result.status == Status.FAIL
    assert any(w.rule.startswith("DRY-000") for w in result.warnings)
    assert any(v.rule.startswith("DRY-001") for v in result.violations)


def test_clean_tree_has_no_findings(tmp_path: Path):
    # Arrange: two distinct functions, no duplication.
    (tmp_path / "a.py").write_text(_DUP_BODY.format(name="alpha"), encoding="utf-8")
    (tmp_path / "b.py").write_text(
        "def beta(x):\n    return x * 2\n",
        encoding="utf-8",
    )

    # Act.
    result = DuplicationCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.violations


def test_short_identical_bodies_below_threshold_are_not_flagged(tmp_path: Path):
    # Arrange: two byte-for-byte identical functions whose bodies hold only four
    # statements, one below the five-statement minimum the check requires.
    short_body = (
        "def {name}(a, b):\n"
        "    total = 0\n"
        "    total = a + b\n"
        "    total = total + 1\n"
        "    return total\n"
    )
    (tmp_path / "a.py").write_text(short_body.format(name="alpha"), encoding="utf-8")
    (tmp_path / "b.py").write_text(short_body.format(name="beta"), encoding="utf-8")

    # Act.
    result = DuplicationCheck().run(src_root=str(tmp_path))

    # Assert: identical but too short, so no DRY-001 duplicate is raised.
    assert result.status == Status.PASS
    assert not result.violations


def test_test_prefixed_files_are_excluded_from_duplication(tmp_path: Path):
    # Arrange: a duplicate pair where both copies live in test_*-prefixed files,
    # which the exclusion rules skip entirely.
    (tmp_path / "test_a.py").write_text(_DUP_BODY.format(name="alpha"), encoding="utf-8")
    (tmp_path / "test_b.py").write_text(_DUP_BODY.format(name="beta"), encoding="utf-8")

    # Act.
    result = DuplicationCheck().run(src_root=str(tmp_path))

    # Assert: excluded files never pair up, so the tree stays clean.
    assert result.status == Status.PASS
    assert not result.violations


def test_root_under_a_skip_named_ancestor_is_still_scanned(tmp_path: Path):
    # Arrange: a genuine duplicate pair in a project checked out under a
    # migrations/ directory, which the exclusion rules name.
    root = tmp_path / "migrations" / "project"
    root.mkdir(parents=True)
    (root / "a.py").write_text(_DUP_BODY.format(name="alpha"), encoding="utf-8")
    (root / "b.py").write_text(_DUP_BODY.format(name="beta"), encoding="utf-8")

    # Act: scan the project, not its ancestor.
    result = DuplicationCheck().run(src_root=str(root))

    # Assert: the ancestor is the user's filesystem, not the project layout.
    assert result.status == Status.FAIL
    assert any(v.rule.startswith("DRY-001") for v in result.violations)


def _write_pair(tmp_path: Path, *, first: str, second: str) -> None:
    """Write two modules holding one function each."""
    (tmp_path / "a.py").write_text(first, encoding="utf-8")
    (tmp_path / "b.py").write_text(second, encoding="utf-8")


_CALLEE_BODY = (
    "def {name}(values, bound):\n"
    "    picked = {callee}(values)\n"
    "    picked = {callee}(picked, bound)\n"
    "    result = picked * 2\n"
    "    result = result + 1\n"
    "    return result\n"
)


def test_opposite_builtin_callees_are_not_clones(tmp_path: Path):
    # Arrange: the same skeleton calling min in one function and max in the
    # other: a different operation, not a renamed variable.
    _write_pair(
        tmp_path,
        first=_CALLEE_BODY.format(name="clamp_upper", callee="min"),
        second=_CALLEE_BODY.format(name="clamp_lower", callee="max"),
    )

    # Act.
    result = DuplicationCheck().run(src_root=str(tmp_path))

    # Assert: the called name is kept, so the pair is distinct.
    assert result.status == Status.PASS
    assert not result.violations


def test_renamed_variables_around_the_same_callee_still_clone(tmp_path: Path):
    # Arrange: the same callee, every variable renamed: a genuine clone.
    _write_pair(
        tmp_path,
        first=_CALLEE_BODY.format(name="clamp_a", callee="min"),
        second=_CALLEE_BODY.format(name="clamp_b", callee="min").replace("picked", "chosen"),
    )

    # Act.
    result = DuplicationCheck().run(src_root=str(tmp_path))

    # Assert: flagged on both sides, at the def lines.
    assert [(v.file, v.line) for v in result.violations] == [("a.py", 1), ("b.py", 1)]
    assert all(v.rule.startswith("DRY-001") for v in result.violations)


_FOUR_WITH_DOCSTRING = (
    "def {name}(a, b):\n"
    '    """{doc}"""\n'
    "    total = a + b\n"
    "    total = total * 2\n"
    "    total = total - 1\n"
    "    return total\n"
)


def test_docstring_does_not_count_towards_the_statement_floor(tmp_path: Path):
    # Arrange: four identical statements each, reaching five only through a
    # docstring, which is documentation rather than logic.
    _write_pair(
        tmp_path,
        first=_FOUR_WITH_DOCSTRING.format(name="alpha", doc="Add, doubled."),
        second=_FOUR_WITH_DOCSTRING.format(name="beta", doc="Another docstring."),
    )

    # Act.
    result = DuplicationCheck().run(src_root=str(tmp_path))

    # Assert: below the floor, so not a clone.
    assert result.status == Status.PASS
    assert not result.violations


def test_docstring_does_not_hide_a_clone(tmp_path: Path):
    # Arrange: a five-statement clone where only one side is documented.
    documented = _DUP_BODY.format(name="alpha").replace(
        "    total = 0\n",
        '    """Documented."""\n    total = 0\n',
    )
    _write_pair(tmp_path, first=documented, second=_DUP_BODY.format(name="beta"))

    # Act.
    result = DuplicationCheck().run(src_root=str(tmp_path))

    # Assert: the docstring is left out of the comparison, so both sides match.
    assert [(v.file, v.line) for v in result.violations] == [("a.py", 1), ("b.py", 1)]
