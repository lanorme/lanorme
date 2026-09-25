"""Tests for ``lanorme.paths``, the shared test-file classification.

Every check that treats test code differently goes through these three
predicates, so their boundaries are pinned here once. The per-check
behaviour (a boundary file exempt from a production rule, or selected by a
test rule) is covered in ``test_test_file_boundaries.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme.paths import is_test_file, is_test_module, is_test_support, is_under_tests_dir


@pytest.mark.parametrize(
    "relative",
    ["tests/test_api.py", "pkg/test_module.py", "test_root.py", "pkg/api_test.py", "test/test_a.py"],
)
def test_module_matches_pytest_collection_names(relative: str) -> None:
    # Act
    matched = is_test_module(relative)

    # Assert: a test_* or *_test stem is a test module wherever it lives.
    assert matched


@pytest.mark.parametrize(
    "relative",
    ["tests/helpers.py", "tests/conftest.py", "tests/__init__.py", "pkg/tests.py", "pkg/testing.py"],
)
def test_module_rejects_support_and_lookalike_names(relative: str) -> None:
    # Act
    matched = is_test_module(relative)

    # Assert: only the collected stem shapes count, not conftest or a "tests" module.
    assert not matched


@pytest.mark.parametrize(
    "relative",
    ["conftest.py", "pkg/conftest.py", "tests/fixtures/users.py", "tests/fixtures.py", "test/factories/a.py"],
)
def test_support_covers_conftest_anywhere_and_fixtures_under_tests(relative: str) -> None:
    # Act
    matched = is_test_support(relative)

    # Assert
    assert matched


@pytest.mark.parametrize("relative", ["factories/user.py", "pkg/fixtures/data.py", "tests/helpers.py"])
def test_support_rejects_fixtures_outside_tests_and_plain_helpers(relative: str) -> None:
    # Act
    matched = is_test_support(relative)

    # Assert: a production factories/ package is not test scaffolding.
    assert not matched


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("tests/helpers.py", True),
        ("test/libregrtest/main.py", True),
        ("pkg/tests/support.py", True),
        ("tests", False),
        ("pkg/tests.py", False),
        ("testing/suite.py", False),
        ("integration_tests/a.py", False),
    ],
)
def test_under_tests_dir_matches_a_directory_part_only(relative: str, expected: bool) -> None:
    # Act
    matched = is_under_tests_dir(relative)

    # Assert: the directory name must be exactly tests or test, and a file named tests does not count.
    assert matched is expected


def test_test_file_is_the_union_of_the_three_predicates() -> None:
    # Arrange: one file per predicate, plus one that matches none.
    by_module, by_support, by_dir, production = (
        "pkg/test_x.py",
        "pkg/conftest.py",
        "tests/helpers.py",
        "pkg/service.py",
    )

    # Act
    verdicts = [is_test_file(p) for p in (by_module, by_support, by_dir, production)]

    # Assert
    assert verdicts == [True, True, True, False]


def test_accepts_path_objects_and_backslashes() -> None:
    # Act
    verdicts = [is_test_file(Path("tests") / "helpers.py"), is_test_file("pkg\\tests\\helpers.py")]

    # Assert: a Path and a Windows-style string classify like the posix string.
    assert verdicts == [True, True]
