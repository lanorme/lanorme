"""Tests for the file_limits check: SIZE-001/002/003, COMPLEXITY-001, PARAM-001.

Each rule is pinned at its boundary. A single scan applies all five rules into
one aggregate result, so every fixture isolates one rule and keeps the other
metrics sub-threshold. Warn and error findings share a rule prefix (for example
"SIZE-002: approaching" versus "SIZE-002: exceeds"), so the warn-versus-fail
distinction is read from which list the finding lands in (``result.warnings``
versus ``result.violations``), never from the rule string alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.file_limits import FileLimitsCheck


@pytest.fixture
def run_on(tmp_path: Path):
    """Write *source* to a neutrally named file and run the check on the tree.

    The filename avoids the test_/__init__/conftest exclusions so the fixture is
    actually scanned.
    """

    def _run(source: str):
        (tmp_path / "sample.py").write_text(source, encoding="utf-8")
        return FileLimitsCheck().run(src_root=str(tmp_path))

    return _run


def _has_rule(findings, prefix: str) -> bool:
    """True if any finding's rule starts with *prefix*."""
    return any(f.rule.startswith(prefix) for f in findings)


# SIZE-001: file effective lines. 299 clean / 300 warn / 499 warn / 500 fail.
# Effective lines are non-blank, non-comment-only. A body of N bare statements
# ("x = 0") yields exactly N effective lines.


def _file_with_effective_lines(count: int) -> str:
    return "".join(f"x = {i}\n" for i in range(count))


def test_size001_below_soft_is_clean(run_on):
    # Arrange: 299 effective lines, one below the 300 warn threshold.
    source = _file_with_effective_lines(299)

    # Act.
    result = run_on(source)

    # Assert.
    assert result.status == Status.PASS
    assert not _has_rule(result.warnings, "SIZE-001")
    assert not _has_rule(result.violations, "SIZE-001")


def test_size001_at_soft_warns(run_on):
    # Arrange: exactly 300 effective lines, the warn boundary.
    source = _file_with_effective_lines(300)

    # Act.
    result = run_on(source)

    # Assert: warns, does not fail.
    assert result.status == Status.WARN
    assert _has_rule(result.warnings, "SIZE-001")
    assert not _has_rule(result.violations, "SIZE-001")


def test_size001_at_hard_fails(run_on):
    # Arrange: exactly 500 effective lines, the error boundary.
    source = _file_with_effective_lines(500)

    # Act.
    result = run_on(source)

    # Assert: fails, the finding is a violation not a warning.
    assert result.status == Status.FAIL
    assert _has_rule(result.violations, "SIZE-001")
    assert not _has_rule(result.warnings, "SIZE-001")


# SIZE-002: function length = end_lineno - lineno + 1.
# 49 clean / 50 warn / 79 warn / 80 fail.


def _function_of_length(length: int) -> str:
    # "def f():" is line 1; the body fills the remaining (length - 1) lines.
    body = "".join(f"    x = {i}\n" for i in range(length - 1))
    return "def f():\n" + body


def test_size002_below_soft_is_clean(run_on):
    # Arrange: a 49-line function, one below the 50 warn threshold.
    source = _function_of_length(49)

    # Act.
    result = run_on(source)

    # Assert.
    assert result.status == Status.PASS
    assert not _has_rule(result.warnings, "SIZE-002")
    assert not _has_rule(result.violations, "SIZE-002")


def test_size002_at_soft_warns(run_on):
    # Arrange: a function spanning exactly 50 lines, the warn boundary.
    source = _function_of_length(50)

    # Act.
    result = run_on(source)

    # Assert: warns, does not fail.
    assert _has_rule(result.warnings, "SIZE-002")
    assert not _has_rule(result.violations, "SIZE-002")


def test_size002_at_hard_fails(run_on):
    # Arrange: a function spanning exactly 80 lines, the error boundary.
    source = _function_of_length(80)

    # Act.
    result = run_on(source)

    # Assert: fails, the finding is a violation.
    assert result.status == Status.FAIL
    assert _has_rule(result.violations, "SIZE-002")
    assert not _has_rule(result.warnings, "SIZE-002")


# SIZE-003: class method count. Warn-only tier: > 10 warns, no fail path.
# 10 clean / 11 warn.


def _class_with_methods(count: int) -> str:
    methods = "".join(f"    def m{i}(self):\n        pass\n" for i in range(count))
    return "class C:\n" + methods


def test_size003_at_limit_is_clean(run_on):
    # Arrange: exactly 10 methods, at the limit but not over it (> is strict).
    source = _class_with_methods(10)

    # Act.
    result = run_on(source)

    # Assert.
    assert result.status == Status.PASS
    assert not _has_rule(result.warnings, "SIZE-003")
    assert not _has_rule(result.violations, "SIZE-003")


def test_size003_past_limit_warns(run_on):
    # Arrange: 11 methods, one past the limit.
    source = _class_with_methods(11)

    # Act.
    result = run_on(source)

    # Assert: warns only; there is no fail tier for SIZE-003.
    assert result.status == Status.WARN
    assert _has_rule(result.warnings, "SIZE-003")
    assert not _has_rule(result.violations, "SIZE-003")


# COMPLEXITY-001: cyclomatic complexity = 1 base + branching nodes.
# 9 clean / 10 warn / 14 warn / 15 fail. Each bare "if a:" adds exactly 1.
# Single-variable conditions avoid BoolOp double-counting.


def _function_of_complexity(complexity: int) -> str:
    # complexity = 1 + number of bare if statements.
    ifs = "".join(f"    if a == {i}:\n        pass\n" for i in range(complexity - 1))
    return "def f(a):\n" + ifs + "    return a\n"


def test_complexity001_below_soft_is_clean(run_on):
    # Arrange: complexity 9 (eight bare ifs), one below the 10 warn threshold.
    source = _function_of_complexity(9)

    # Act.
    result = run_on(source)

    # Assert.
    assert result.status == Status.PASS
    assert not _has_rule(result.warnings, "COMPLEXITY-001")
    assert not _has_rule(result.violations, "COMPLEXITY-001")


def test_complexity001_at_soft_warns(run_on):
    # Arrange: complexity exactly 10, the warn boundary.
    source = _function_of_complexity(10)

    # Act.
    result = run_on(source)

    # Assert: warns, does not fail.
    assert _has_rule(result.warnings, "COMPLEXITY-001")
    assert not _has_rule(result.violations, "COMPLEXITY-001")


def test_complexity001_at_hard_fails(run_on):
    # Arrange: complexity exactly 15, the error boundary.
    source = _function_of_complexity(15)

    # Act.
    result = run_on(source)

    # Assert: fails, the finding is a violation.
    assert result.status == Status.FAIL
    assert _has_rule(result.violations, "COMPLEXITY-001")
    assert not _has_rule(result.warnings, "COMPLEXITY-001")


# PARAM-001: parameter count excluding self/cls.
# 4 clean / 5 warn / 7 warn / 8 fail.


def test_param001_below_soft_is_clean(run_on):
    # Arrange: a function with 4 parameters, one below the 5 warn threshold.
    source = "def f(a, b, c, d):\n    return a\n"

    # Act.
    result = run_on(source)

    # Assert.
    assert result.status == Status.PASS
    assert not _has_rule(result.warnings, "PARAM-001")
    assert not _has_rule(result.violations, "PARAM-001")


def test_param001_at_soft_warns(run_on):
    # Arrange: exactly 5 parameters, the warn boundary.
    source = "def f(a, b, c, d, e):\n    return a\n"

    # Act.
    result = run_on(source)

    # Assert: warns, does not fail.
    assert _has_rule(result.warnings, "PARAM-001")
    assert not _has_rule(result.violations, "PARAM-001")


def test_param001_at_hard_fails(run_on):
    # Arrange: exactly 8 parameters, the error boundary.
    source = "def f(a, b, c, d, e, g, h, i):\n    return a\n"

    # Act.
    result = run_on(source)

    # Assert: fails, the finding is a violation.
    assert result.status == Status.FAIL
    assert _has_rule(result.violations, "PARAM-001")
    assert not _has_rule(result.warnings, "PARAM-001")


def test_param001_excludes_self_so_four_real_params_is_clean(run_on):
    # Arrange: a method with self plus 4 real parameters. self is excluded, so
    # the effective count is 4, below the warn threshold. If self were counted
    # the effective count would be 5 and this would warn, so this asserts the
    # exclusion is actually in effect.
    source = "class C:\n    def m(self, a, b, c, d):\n        return a\n"

    # Act.
    result = run_on(source)

    # Assert: clean despite five declared arguments.
    assert result.status == Status.PASS
    assert not _has_rule(result.warnings, "PARAM-001")
    assert not _has_rule(result.violations, "PARAM-001")


def test_param001_self_plus_five_real_params_warns(run_on):
    # Arrange: self plus 5 real parameters. self is excluded, so the effective
    # count is exactly 5, the warn boundary, with the exclusion still applied.
    source = "class C:\n    def m(self, a, b, c, d, e):\n        return a\n"

    # Act.
    result = run_on(source)

    # Assert: warns, does not fail.
    assert _has_rule(result.warnings, "PARAM-001")
    assert not _has_rule(result.violations, "PARAM-001")
