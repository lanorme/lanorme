"""White-box unit tests for the cyclomatic-complexity metric.

These tests are deliberately implementation-coupled: they import the private
``_cyclomatic_complexity`` helper and assert its exact integer for one AST
construct at a time. They exist because some per-construct distinctions (which
``match`` pattern kinds are refutable, async comprehensions, generator
expressions) carry a complexity of only 1-3, far below the warn threshold of 10,
so they emit no finding and cannot be observed through the public check without
artificially amplifying every case. Keeping them here, isolated and labelled,
documents the precise contract of the metric.

The BEHAVIOURAL guarantees -- that filters, nested comprehension loops, refutable
``match`` arms and nested-function exclusion change what the check reports -- live
in ``test_file_limits.py`` and run through the public ``FileLimitsCheck``. If an
internal rename breaks this file but those stay green, the behaviour is intact and
only this contract needs updating.
"""

from __future__ import annotations

import ast

import pytest

from lanorme.checks.file_limits import _cyclomatic_complexity


def _complexity_of(source: str) -> int:
    """Cyclomatic complexity of the single function defined in ``source``."""
    func = ast.parse(source).body[0]
    assert isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef)
    return _cyclomatic_complexity(func_node=func)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # A comprehension's primary loop is a single expression: it does not count.
        ("def f(xs):\n return [x for x in xs]", 1),
        # A filter `if` is a branch; a nested `for` is one too, the primary is not.
        ("def f(xs):\n return [x for x in xs if p(x)]", 2),
        ("def f(xs, ys):\n return [x for x in xs for y in ys]", 2),
        # Multiple filters in one generator each count; the `and` (BoolOp) adds one.
        ("def f(xs):\n return [x for x in xs if a if b]", 3),
        ("def f(xs):\n return [x for x in xs if a and b]", 3),
        # Set, dict and generator-expression forms count their filters identically.
        ("def f(xs):\n return {k for k in xs if k}", 2),
        ("def f(xs):\n return {k: v for k, v in xs if k}", 2),
        ("def f(xs):\n return sum(x for x in xs if x)", 2),
        # Async comprehensions count their nested `for` and filter `if`.
        ("async def f(xs, ys):\n return [x async for x in xs for y in ys if y]", 3),
        # A nested comprehension: each primary `for` is free, the inner filter counts.
        ("def f(rows):\n return [[y for y in r if y] for r in rows]", 2),
        # Every refutable `match` pattern kind counts one: literal, or-pattern,
        ("def f(x):\n match x:\n  case 1: return 1\n  case 2: return 2", 3),
        ("def f(x):\n match x:\n  case 1 | 2 | 3: return 1\n  case _: return 0", 2),
        # sequence, class and mapping patterns,
        ("def f(x):\n match x:\n  case [a, b]: return 1\n  case _: return 0", 2),
        ("def f(x):\n match x:\n  case Point(x=0): return 1\n  case _: return 0", 2),
        ("def f(x):\n match x:\n  case {'k': v}: return 1\n  case _: return 0", 2),
        # and a capture guarded by a condition (the guard can fail, so it counts).
        ("def f(x):\n match x:\n  case y if y > 0: return 1\n  case _: return 0", 2),
        # An irrefutable catch-all is free, whether wildcard or bare capture.
        ("def f(x):\n match x:\n  case 1: return 1\n  case _: return 0", 2),
        ("def f(x):\n match x:\n  case 1: return 1\n  case other: return other", 2),
    ],
)
def test_metric_increment_per_construct(source, expected):
    # Arrange: a function whose only branching is one construct under test.
    # Act.
    value = _complexity_of(source)
    # Assert: the construct contributes exactly the documented increment.
    assert value == expected
