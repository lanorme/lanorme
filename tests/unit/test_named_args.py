"""Tests for the named_args check (KWARG-001).

KWARG-001 enforces a bare ``*`` separator on every function/method with more
than one *real* positional parameter (self/cls, positional-only and
``Depends()``-injected params do not count). The rule is OPT-IN: it stays
silent until enabled via ``[tool.lanorme.named_args] enabled = true``.

These tests use the direct-instantiation idiom (mirroring
``tests/unit/test_comments.py``): build a ``NamedArgsCheck``, flip
``enabled``, and call ``run(src_root=...)`` against files under ``tmp_path``.

Confirmed intent-questions are intentionally NOT encoded as passing tests:
  * ``*args`` functions fire with an unsatisfiable fix (precision concern).
  * the docstring's ``__init__`` parenthetical vs the unconditional dunder
    skip. The dunder skip is pinned below as current (precision-preserving)
    behaviour with an explicit comment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.named_args import NamedArgsCheck


@pytest.fixture
def check() -> NamedArgsCheck:
    """A named_args check explicitly enabled (the rule is default-off)."""
    instance = NamedArgsCheck()
    instance.enabled = True
    return instance


def _write(*, root: Path, name: str, body: str) -> None:
    """Write a Python source file under *root*."""
    (root / name).write_text(body, encoding="utf-8")


def _kwarg_hits(result) -> list:
    """Return the KWARG-001 violations from a check result."""
    return [v for v in result.violations if v.rule.startswith("KWARG-001")]


def test_disabled_by_default_stays_silent(tmp_path: Path):
    # Arrange: a clear violation but a check left at its default (disabled) state.
    _write(root=tmp_path, name="x.py", body="def f(a, b):\n    return a\n")
    instance = NamedArgsCheck()

    # Act.
    result = instance.run(src_root=str(tmp_path))

    # Assert: opt-in means PASS with no findings until explicitly enabled.
    assert result.status == Status.PASS
    assert not result.violations


def test_true_positive_two_positional_params_fires(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: two real positional params, no bare * separator.
    _write(root=tmp_path, name="tp.py", body="def transfer(amount, currency):\n    return amount\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: fires, and the message reports the param count.
    assert result.status == Status.FAIL
    hits = _kwarg_hits(result)
    assert len(hits) == 1
    assert "transfer" in hits[0].message
    assert "2 positional params" in hits[0].message


def test_true_positive_async_and_staticmethod_fire(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: an async function and a staticmethod (no self), both with two params.
    body = (
        "async def fetch(a, b):\n    return a\n\n\n"
        "class C:\n    @staticmethod\n    def make(a, b):\n        return a\n"
    )
    _write(root=tmp_path, name="more.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: both are flagged.
    names = {v.message.split("'")[1] for v in _kwarg_hits(result)}
    assert {"fetch", "make"} <= names


def test_true_positive_nested_function_fires(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: a nested inner function with two params (ast.walk descends into it).
    body = "def outer():\n    def inner(a, b):\n        return a\n    return inner\n"
    _write(root=tmp_path, name="nested.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the inner function is reached and flagged.
    hits = _kwarg_hits(result)
    assert len(hits) == 1
    assert "inner" in hits[0].message


def test_bare_star_and_single_param_stay_silent(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: all-keyword-only, a single param, and self + one real param.
    body = (
        "def transfer(*, amount, currency):\n    return amount\n\n\n"
        "def single(amount):\n    return amount\n\n\n"
        "def receiver(self, amount):\n    return amount\n"
    )
    _write(root=tmp_path, name="tn.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: none of these have >1 real positional param.
    assert result.status == Status.PASS
    assert not _kwarg_hits(result)


def test_depends_injected_params_are_exempt(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: Depends() as default, qualified Depends, and Annotated[..., Depends].
    body = (
        "def endpoint(user, db=Depends(get_db)):\n    return user\n\n\n"
        "def endpoint2(user, db: Annotated[DB, Depends(get_db)]):\n    return user\n\n\n"
        "def endpoint3(user, db=fastapi.Depends(get_db)):\n    return user\n"
    )
    _write(root=tmp_path, name="depends.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: each leaves a single real param, so nothing fires.
    assert result.status == Status.PASS
    assert not _kwarg_hits(result)


def test_dunder_methods_are_skipped(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: __init__ and __eq__ with more than one extra param.
    # NOTE: the implementation skips ALL dunders unconditionally; this pins that
    # precision-preserving behaviour. (The docstring's "__init__ with <=1 extra
    # param" wording is an open intent question, tracked in findings, not here.)
    body = (
        "class C:\n"
        "    def __init__(self, a, b):\n        self.a = a\n\n"
        "    def __eq__(self, other, extra):\n        return True\n"
    )
    _write(root=tmp_path, name="dunder.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: dunders never fire.
    assert result.status == Status.PASS
    assert not _kwarg_hits(result)


def test_positional_only_params_not_counted(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: two positional-only params (before /) that can never be keyword.
    _write(root=tmp_path, name="posonly.py", body="def f(a, b, /):\n    return a\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: positional-only params are excluded from the count.
    assert result.status == Status.PASS
    assert not _kwarg_hits(result)


def test_positional_only_plus_regular_counts_only_regular(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: two positional-only (a, b) plus two regular (c, d).
    _write(root=tmp_path, name="mix.py", body="def f(a, b, /, c, d):\n    return a\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: only c and d count, so it fires reporting 2.
    hits = _kwarg_hits(result)
    assert len(hits) == 1
    assert "2 positional params" in hits[0].message


def test_noqa_on_def_line_suppresses(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: a multi-line signature whose def line carries the noqa.
    body = "def legacy(  # noqa: KWARG-001\n    a,\n    b,\n):\n    return a\n"
    _write(root=tmp_path, name="ok.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the def-line noqa silences the finding.
    assert result.status == Status.PASS
    assert not _kwarg_hits(result)


def test_noqa_on_continuation_line_does_not_suppress(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: same signature but the noqa sits on an argument (continuation) line.
    body = "def legacy(\n    a,\n    b,  # noqa: KWARG-001\n):\n    return a\n"
    _write(root=tmp_path, name="cont.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: only the def line is inspected for noqa, so it still fires.
    assert result.status == Status.FAIL
    assert _kwarg_hits(result)


def test_kwargs_does_not_exempt_real_params(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: two real params followed by **kwargs (a satisfiable, fixable case).
    _write(root=tmp_path, name="kw.py", body="def g(a, b, **kwargs):\n    return a\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: a and b are still counted (def g(*, a, b, **kwargs) would comply).
    hits = _kwarg_hits(result)
    assert len(hits) == 1
    assert "2 positional params" in hits[0].message


def test_single_real_param_with_varargs_stays_silent(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: one real positional param alongside *args and **kwargs.
    _write(root=tmp_path, name="one.py", body="def h(a, *args, **kwargs):\n    return a\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: only one real positional param, so nothing fires.
    assert result.status == Status.PASS
    assert not _kwarg_hits(result)


def test_test_prefixed_files_are_skipped(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: a violation living in a file whose name starts with test_.
    _write(root=tmp_path, name="test_thing.py", body="def helper(a, b):\n    return a\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: test files are exempt entirely.
    assert result.status == Status.PASS
    assert not _kwarg_hits(result)


def test_unparseable_file_warns_without_crashing(check: NamedArgsCheck, tmp_path: Path):
    # Arrange: a file with a syntax error and nothing else to scan.
    _write(root=tmp_path, name="broken.py", body="def broken(a, b:\n    return a\n")

    # Act: the run must complete rather than raise SyntaxError.
    result = check.run(src_root=str(tmp_path))

    # Assert: it degrades to a WARN-level finding, no violations, no crash.
    assert result.status == Status.WARN
    assert not result.violations
    assert any("parse error" in w.rule for w in result.warnings)
