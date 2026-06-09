"""The repeatable coverage standard: every registered check carries its own tests.

This is a meta-test, not a check of any one rule. It asserts a *process*
guarantee: you cannot add a check to the registry without also adding a
dedicated ``tests/unit/test_<name>.py`` that exercises it. The gap this closes
is the one that let a swathe of checks (prose, restating, layer_deps, ...) ship
with config-only or zero behavioural coverage. Keep this green and the gap
cannot silently reopen.

Tests follow AAA structure with inline ``# Arrange / # Act / # Assert`` markers
so AAA-001 passes when LaNorme dogfoods itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lanorme import get_all_checks
from lanorme.cli import _load_builtin_checks

_TESTS_DIR = Path(__file__).parent
_MIN_TESTS_PER_CHECK = 3


def _registered_check_names() -> list[str]:
    """All built-in checks, with discovery forced so the registry is populated."""
    _load_builtin_checks()
    return sorted(get_all_checks())


def _test_function_count(path: Path) -> int:
    """Number of top-level ``test_*`` functions in a test module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )


@pytest.mark.parametrize("name", _registered_check_names())
def test_every_check_has_a_dedicated_test_file(name: str):
    # Arrange: the test file a check is expected to own.
    expected = _TESTS_DIR / f"test_{name}.py"

    # Act / Assert: it must exist, so no check ships untested.
    assert expected.is_file(), (
        f"check {name!r} has no dedicated test file; expected {expected.name}. "
        f"Every registered check must carry behavioural tests."
    )


@pytest.mark.parametrize("name", _registered_check_names())
def test_each_check_test_file_is_not_a_stub(name: str):
    # Arrange.
    path = _TESTS_DIR / f"test_{name}.py"
    if not path.is_file():
        pytest.skip("covered by test_every_check_has_a_dedicated_test_file")

    # Act.
    count = _test_function_count(path)

    # Assert: a real suite, not a placeholder with one happy-path assertion.
    assert count >= _MIN_TESTS_PER_CHECK, (
        f"{path.name} has only {count} test(s); a check needs at least "
        f"{_MIN_TESTS_PER_CHECK} (true positive, true negative, and a boundary)."
    )
