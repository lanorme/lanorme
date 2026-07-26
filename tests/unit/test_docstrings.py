"""Tests for the docstrings check (CMT-006 / CMT-007, opt-in).

CMT-006 requires a docstring on a public definition past a size floor; CMT-007
requires that docstring to say something the signature does not. CMT-007 is the
anti-gaming half, so the suite is weighted toward its precision: flagging a
docstring that carries real information is the cardinal sin, because the rule's
whole purpose is to make writing one worthwhile.

The check is default-off; every test enables it via ``configure`` and drives the
check object directly. Assertions key on the rule code, never on message text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.docstrings import DocstringsCheck

# A body long enough to clear the default size floor, so scope is never the
# reason a case passes or fails.
LONG_BODY = """    first = 1
    second = 2
    third = 3
    return first + second + third
"""


@pytest.fixture
def check() -> DocstringsCheck:
    """An enabled docstrings check (it is default-off otherwise)."""
    instance = DocstringsCheck()
    instance.configure(settings={"enabled": True})
    return instance


def _write(*, root: Path, body: str) -> None:
    """Write *body* as the only module under *root*."""
    (root / "sample.py").write_text(body, encoding="utf-8")


def _codes(*, result) -> list[str]:
    """The rule codes of all violations on *result*."""
    return [v.code for v in result.violations]


def _func(*, name: str = "calculate_total", params: str = "items", doc: str | None) -> str:
    """Build a module holding one function, optionally documented."""
    docline = "" if doc is None else f'    """{doc}"""\n'
    return f"def {name}({params}):\n{docline}{LONG_BODY}"


# --------------------------------------------------------------------------- #
# Default-off
# --------------------------------------------------------------------------- #


def test_disabled_by_default(tmp_path: Path) -> None:
    # Arrange
    _write(root=tmp_path, body=_func(doc=None))

    # Act
    result = DocstringsCheck().run(src_root=str(tmp_path))

    # Assert
    assert result.status is Status.PASS
    assert result.violations == []


# --------------------------------------------------------------------------- #
# CMT-006: the docstring must exist
# --------------------------------------------------------------------------- #


def test_missing_docstring_is_flagged(tmp_path: Path, check: DocstringsCheck) -> None:
    _write(root=tmp_path, body=_func(doc=None))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _codes(result=result) == ["CMT-006"]


def test_short_definition_needs_no_docstring(tmp_path: Path, check: DocstringsCheck) -> None:
    _write(root=tmp_path, body="def tiny(value):\n    return value\n")

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_private_definition_is_out_of_scope_by_default(tmp_path: Path, check: DocstringsCheck) -> None:
    _write(root=tmp_path, body=_func(name="_helper", doc=None))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_private_definition_flagged_when_configured(tmp_path: Path) -> None:
    # Arrange
    check = DocstringsCheck()
    check.configure(settings={"enabled": True, "require_private": True})
    _write(root=tmp_path, body=_func(name="_helper", doc=None))

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _codes(result=result) == ["CMT-006"]


def test_min_lines_is_configurable(tmp_path: Path) -> None:
    # Arrange
    check = DocstringsCheck()
    check.configure(settings={"enabled": True, "min_lines": 100})
    _write(root=tmp_path, body=_func(doc=None))

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


# --------------------------------------------------------------------------- #
# CMT-007: the docstring must say something
# --------------------------------------------------------------------------- #


def test_docstring_restating_the_name_is_flagged(tmp_path: Path, check: DocstringsCheck) -> None:
    _write(root=tmp_path, body=_func(doc="Calculate the total."))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _codes(result=result) == ["CMT-007"]


def test_abbreviated_name_is_still_a_restatement(tmp_path: Path, check: DocstringsCheck) -> None:
    _write(root=tmp_path, body=_func(name="proc", params="data", doc="Process the data."))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _codes(result=result) == ["CMT-007"]


def test_empty_docstring_is_flagged(tmp_path: Path, check: DocstringsCheck) -> None:
    _write(root=tmp_path, body=_func(doc=""))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _codes(result=result) == ["CMT-007"]


def test_pure_filler_is_flagged(tmp_path: Path, check: DocstringsCheck) -> None:
    _write(root=tmp_path, body=_func(doc="This is a helper function."))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _codes(result=result) == ["CMT-007"]


def test_method_restating_its_class_is_flagged(tmp_path: Path, check: DocstringsCheck) -> None:
    body = (
        "class Bucket:\n"
        '    """Token bucket sized so an idle key costs nothing to keep."""\n\n'
        "    def refill(self, elapsed):\n"
        '        """Refill the bucket."""\n'
        "        self.tokens = elapsed\n"
        "        self.stamp = elapsed\n"
        "        self.dirty = True\n"
        "        return self.tokens\n"
    )
    # Arrange
    _write(root=tmp_path, body=body)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert _codes(result=result) == ["CMT-007"]


# --------------------------------------------------------------------------- #
# CMT-007 precision traps: these must never be flagged
# --------------------------------------------------------------------------- #


def test_docstring_adding_a_unit_is_kept(tmp_path: Path, check: DocstringsCheck) -> None:
    _write(root=tmp_path, body=_func(doc="Total in minor units, never a rounded float."))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_docstring_adding_a_why_is_kept(tmp_path: Path, check: DocstringsCheck) -> None:
    _write(root=tmp_path, body=_func(doc="Calculate the total, so that callers avoid a second pass."))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_docstring_with_a_reference_is_kept(tmp_path: Path, check: DocstringsCheck) -> None:
    _write(root=tmp_path, body=_func(doc="Calculate the total. See issue #412 for the rounding rule."))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_short_name_does_not_swallow_unrelated_words(tmp_path: Path, check: DocstringsCheck) -> None:
    _write(root=tmp_path, body=_func(name="go", params="target", doc="Govern the retry cadence."))

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_dunder_is_out_of_scope(tmp_path: Path, check: DocstringsCheck) -> None:
    body = (
        "class Cache:\n"
        '    """Entries evicted on read rather than on a timer."""\n\n'
        "    def __init__(self, capacity):\n"
        '        """Init."""\n'
        "        self.capacity = capacity\n"
        "        self.entries = {}\n"
        "        self.hits = 0\n"
        "        self.misses = 0\n"
    )
    # Arrange
    _write(root=tmp_path, body=body)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []


def test_test_files_are_skipped(tmp_path: Path, check: DocstringsCheck) -> None:
    (tmp_path / "test_thing.py").write_text(_func(doc=None), encoding="utf-8")

    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == []
