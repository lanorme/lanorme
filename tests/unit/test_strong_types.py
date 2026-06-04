"""Tests for TYPE-001 through TYPE-003 strong typing discipline.

The regression of note: a function annotated with a deeply nested union
(`dict[str, int | int | ... | int]` with thousands of terms) overflowed the
recursive ``_collect_value_names`` walk and crashed the whole run. One bad file
must be skipped with an advisory warning, not be fatal, and the rest of the tree
must still be analysed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.strong_types import StrongTypesCheck


@pytest.fixture
def deep_union_source() -> str:
    """A file that parses but overflows the recursive annotation walk."""
    union = " | ".join(["int"] * 4000)
    return f"def f(x: dict[str, {union}]) -> None: ...\n"


def test_deeply_nested_annotation_is_skipped_not_crashed(
    tmp_path: Path, deep_union_source: str
):
    # Arrange: a deep-union file that overflows _collect_value_names, beside a
    # genuine TYPE-001 violation that must still be reported.
    (tmp_path / "deep.py").write_text(deep_union_source, encoding="utf-8")
    (tmp_path / "weak.py").write_text(
        "from typing import Any\n\n\ndef g(x: dict[str, Any]) -> None: ...\n",
        encoding="utf-8",
    )

    # Act: the run must complete rather than raise RecursionError.
    result = StrongTypesCheck().run(src_root=str(tmp_path))

    # Assert: the deep file is skipped with a TYPE-000 warning, and the genuine
    # weakly-typed dict elsewhere is still detected.
    assert result.status == Status.FAIL
    assert any(w.rule.startswith("TYPE-000") for w in result.warnings)
    assert any(v.rule.startswith("TYPE-001") for v in result.violations)


def test_type001_any_leaf_is_violation(tmp_path: Path):
    # Arrange: a dict with an Any value, the canonical weakly-typed container.
    (tmp_path / "m.py").write_text(
        "from typing import Any\n\n\ndef handler(payload: dict[str, Any]) -> None: ...\n",
        encoding="utf-8",
    )

    # Act.
    result = StrongTypesCheck().run(src_root=str(tmp_path))

    # Assert: a hard TYPE-001 failure naming the parameter.
    assert result.status == Status.FAIL
    type001 = [v for v in result.violations if v.rule == "TYPE-001"]
    assert len(type001) == 1
    assert "payload" in type001[0].message


def test_type001_object_leaf_is_placeholder_warning(tmp_path: Path):
    # Arrange: the boundary case: `object` is a soft placeholder, not a fail.
    (tmp_path / "m.py").write_text(
        "def handler(payload: dict[str, object]) -> None: ...\n",
        encoding="utf-8",
    )

    # Act.
    result = StrongTypesCheck().run(src_root=str(tmp_path))

    # Assert: warned, not failed.
    assert result.status == Status.WARN
    assert not result.violations
    assert any(w.rule == "TYPE-001" for w in result.warnings)


def test_type001_concrete_dict_is_clean(tmp_path: Path):
    # Arrange: negative case: a fully concrete container is fine.
    (tmp_path / "m.py").write_text(
        "def handler(payload: dict[str, int]) -> None: ...\n",
        encoding="utf-8",
    )

    # Act.
    result = StrongTypesCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.violations
    assert not result.warnings


def test_type002_bare_container_is_violation(tmp_path: Path):
    # Arrange: a bare `dict` annotation with no type parameters.
    (tmp_path / "m.py").write_text(
        "def handler(payload: dict) -> None: ...\n",
        encoding="utf-8",
    )

    # Act.
    result = StrongTypesCheck().run(src_root=str(tmp_path))

    # Assert: a TYPE-002 failure.
    assert result.status == Status.FAIL
    type002 = [v for v in result.violations if v.rule == "TYPE-002"]
    assert len(type002) == 1
    assert "payload" in type002[0].message


def test_type002_parametrised_container_is_clean(tmp_path: Path):
    # Arrange: negative case: the parametrised form is acceptable.
    (tmp_path / "m.py").write_text(
        "def handler(items: list[int]) -> None: ...\n",
        encoding="utf-8",
    )

    # Act.
    result = StrongTypesCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.violations


def test_type003_untyped_kwargs_is_violation(tmp_path: Path):
    # Arrange: bare `**kwargs` with no annotation.
    (tmp_path / "m.py").write_text(
        "def handler(**kwargs) -> None: ...\n",
        encoding="utf-8",
    )

    # Act.
    result = StrongTypesCheck().run(src_root=str(tmp_path))

    # Assert: a TYPE-003 failure naming the kwargs parameter.
    assert result.status == Status.FAIL
    type003 = [v for v in result.violations if v.rule == "TYPE-003"]
    assert len(type003) == 1
    assert "kwargs" in type003[0].message


def test_type003_any_kwargs_is_violation(tmp_path: Path):
    # Arrange: boundary case: annotated, but with the forbidden `Any`.
    (tmp_path / "m.py").write_text(
        "from typing import Any\n\n\ndef handler(**kwargs: Any) -> None: ...\n",
        encoding="utf-8",
    )

    # Act.
    result = StrongTypesCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    assert any(v.rule == "TYPE-003" for v in result.violations)


def test_type003_concrete_kwargs_is_clean(tmp_path: Path):
    # Arrange: negative case: a concretely typed **kwargs is fine.
    (tmp_path / "m.py").write_text(
        "def handler(**kwargs: int) -> None: ...\n",
        encoding="utf-8",
    )

    # Act.
    result = StrongTypesCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.violations
