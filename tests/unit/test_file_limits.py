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
from lanorme.scan import Scan


@pytest.fixture
def run_on(tmp_path: Path):
    """Write *source* to a neutrally named file and run the check on the tree.

    The filename avoids the test_/__init__/conftest exclusions so the fixture is
    actually scanned.
    """

    def _run(source: str):
        (tmp_path / "sample.py").write_text(source, encoding="utf-8")
        return FileLimitsCheck().check(Scan(root=tmp_path))

    return _run


def _has_rule(findings, prefix: str) -> bool:
    """True if any finding's rule starts with *prefix*."""
    return any(f.rule.startswith(prefix) for f in findings)


# SIZE-001: file effective lines. 299 clean / 300 warn / 499 warn / 500 fail.
# Effective lines are non-blank, non-comment-only. A body of N bare statements
# ("x = 0") yields exactly N effective lines.


def _build_file_with_effective_lines(count: int) -> str:
    return "".join(f"x = {i}\n" for i in range(count))


def test_size001_below_soft_is_clean(run_on):
    # Arrange: 299 effective lines, one below the 300 warn threshold.
    source = _build_file_with_effective_lines(299)

    # Act.
    result = run_on(source)

    # Assert.
    assert result.status == Status.PASS
    assert not _has_rule(result.warnings, "SIZE-001")
    assert not _has_rule(result.violations, "SIZE-001")


def test_size001_at_soft_warns(run_on):
    # Arrange: exactly 300 effective lines, the warn boundary.
    source = _build_file_with_effective_lines(300)

    # Act.
    result = run_on(source)

    # Assert: warns, does not fail.
    assert result.status == Status.WARN
    assert _has_rule(result.warnings, "SIZE-001")
    assert not _has_rule(result.violations, "SIZE-001")


def test_size001_at_hard_fails(run_on):
    # Arrange: exactly 500 effective lines, the error boundary.
    source = _build_file_with_effective_lines(500)

    # Act.
    result = run_on(source)

    # Assert: fails, the finding is a violation not a warning.
    assert result.status == Status.FAIL
    assert _has_rule(result.violations, "SIZE-001")
    assert not _has_rule(result.warnings, "SIZE-001")


# SIZE-002: function effective lines, mirroring SIZE-001 semantics. Blank and
# comment-only lines inside the function span do not count.
# 49 clean / 50 warn / 79 warn / 80 fail.


def _build_function_with_effective_lines(count: int) -> str:
    # "def f():" is one effective line; the body supplies the remaining
    # (count - 1) effective lines as bare statements.
    body = "".join(f"    x = {i}\n" for i in range(count - 1))
    return "def f():\n" + body


def test_size002_below_soft_is_clean(run_on):
    # Arrange: a 49-effective-line function, one below the 50 warn threshold.
    source = _build_function_with_effective_lines(49)

    # Act.
    result = run_on(source)

    # Assert.
    assert result.status == Status.PASS
    assert not _has_rule(result.warnings, "SIZE-002")
    assert not _has_rule(result.violations, "SIZE-002")


def test_size002_at_soft_warns(run_on):
    # Arrange: a function of exactly 50 effective lines, the warn boundary.
    source = _build_function_with_effective_lines(50)

    # Act.
    result = run_on(source)

    # Assert: warns, does not fail.
    assert _has_rule(result.warnings, "SIZE-002")
    assert not _has_rule(result.violations, "SIZE-002")


def test_size002_at_hard_fails(run_on):
    # Arrange: a function of exactly 80 effective lines, the error boundary.
    source = _build_function_with_effective_lines(80)

    # Act.
    result = run_on(source)

    # Assert: fails, the finding is a violation.
    assert result.status == Status.FAIL
    assert _has_rule(result.violations, "SIZE-002")
    assert not _has_rule(result.warnings, "SIZE-002")


def test_size002_comment_padding_below_soft_stays_clean(run_on):
    # Arrange: 49 effective lines padded with comments and blanks so the raw
    # span (55 lines) crosses the 50-line warn threshold. Under the old
    # raw-span counting this warned; effective counting keeps it clean.
    padding = "    # why: context for the reader\n\n" * 3
    body = "".join(f"    x = {i}\n" for i in range(48))
    source = "def f():\n" + padding + body

    # Act.
    result = run_on(source)

    # Assert.
    assert result.status == Status.PASS
    assert not _has_rule(result.warnings, "SIZE-002")
    assert not _has_rule(result.violations, "SIZE-002")


def test_size002_comments_do_not_push_over_hard_limit(run_on):
    # Arrange: 78 effective lines plus enough comment and blank padding to
    # push the raw span (88 lines) past the 80-line hard limit. The issue #39
    # regression: adding a why-comment must never flip a function into a
    # violation.
    padding = "    # why: explains the next block\n\n" * 5
    body = "".join(f"    x = {i}\n" for i in range(77))
    source = "def f():\n" + padding + body

    # Act.
    result = run_on(source)

    # Assert: no violation; 78 effective lines still warns (50 threshold).
    assert not _has_rule(result.violations, "SIZE-002")
    assert _has_rule(result.warnings, "SIZE-002")


def test_size002_eighty_effective_lines_with_padding_still_fails(run_on):
    # Arrange: 80 effective lines interleaved with comments and blanks. The
    # padding must not dilute the count below the error boundary.
    body = "".join(f"    x = {i}\n" for i in range(79))
    source = "def f():\n    # setup\n\n" + body

    # Act.
    result = run_on(source)

    # Assert: fails, the finding is a violation.
    assert result.status == Status.FAIL
    assert _has_rule(result.violations, "SIZE-002")
    assert not _has_rule(result.warnings, "SIZE-002")


def test_size002_docstring_lines_do_not_count(run_on):
    # Arrange: def line + a three-line docstring + 48 statements. The docstring
    # documents the function rather than lengthening it, so the count is 49,
    # one below the warn boundary; the same body with one more statement is 50.
    body = "".join(f"    x = {i}\n" for i in range(48))
    docstring = '    """Docstring.\n\n    More words.\n    """\n'
    source = "def f():\n" + docstring + body

    # Act.
    result = run_on(source)
    at_boundary = run_on(source + "    x = 48\n")

    # Assert: the docstring never pushes a function over the threshold.
    assert not _has_rule(result.warnings, "SIZE-002")
    assert _has_rule(at_boundary.warnings, "SIZE-002")


def test_size002_long_docstring_on_a_short_function_is_clean(run_on):
    # Arrange: a two-statement function whose docstring alone is 60 lines.
    docstring = "".join(f"    Line {i} of the documentation.\n" for i in range(60))
    source = (
        'def documented(value):\n    """Explain.\n\n'
        + docstring
        + '    """\n'
        + ("    result = value * 2\n    return result\n")
    )

    # Act.
    result = run_on(source)

    # Assert: documentation is not length.
    assert not _has_rule(result.warnings, "SIZE-002")
    assert not _has_rule(result.violations, "SIZE-002")


def test_param001_metaclass_receiver_is_not_a_parameter(run_on):
    # Arrange: a metaclass names its receiver mcs / metacls; four real
    # parameters plus **kwargs is one below the warn threshold, like self.
    source = (
        "class Meta(type):\n"
        "    def __new__(mcs, name, bases, namespace, **kwargs):\n"
        "        return super().__new__(mcs, name, bases, namespace)\n\n"
        "    def __init__(metacls, name, bases, namespace, **kwargs):\n"
        "        super().__init__(name, bases, namespace)\n\n"
        "    def build(this, name, bases, namespace, **kwargs):\n"
        "        return None\n"
    )

    # Act.
    result = run_on(source)

    # Assert: only the unconventional receiver name counts as a parameter.
    param = [w for w in result.warnings if w.rule.startswith("PARAM-001")]
    assert [(w.line, w.message.split("'")[1]) for w in param] == [(8, "build")]
