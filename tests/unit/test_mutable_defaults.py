"""Smoke tests for the MUTDEF-001 mutable default arguments check."""

from __future__ import annotations

from lanorme.checks.mutable_defaults import MutableDefaultsCheck


def _codes(violations) -> set[str]:
    # Arrange / Act / Assert are inside each test; this is a pure helper.
    return {v.rule for v in violations}


def test_mutdef001_fires_on_list_default(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="def process(items=[]):\n    return items\n",
    )

    # Act
    result = MutableDefaultsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "MUTDEF-001" in _codes(result.violations)


def test_mutdef001_fires_on_dict_default(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="def process(options={}):\n    return options\n",
    )

    # Act
    result = MutableDefaultsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "MUTDEF-001" in _codes(result.violations)


def test_mutdef001_fires_on_set_default(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="def process(seen=set()):\n    return seen\n",
    )

    # Act
    result = MutableDefaultsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "MUTDEF-001" in _codes(result.violations)


def test_mutdef001_does_not_fire_on_none_default(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="def process(items=None):\n    if items is None:\n        items = []\n    return items\n",
    )

    # Act
    result = MutableDefaultsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "MUTDEF-001" not in _codes(result.violations)


def test_mutdef001_does_not_fire_on_int_default(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="def process(count=0):\n    return count\n",
    )

    # Act
    result = MutableDefaultsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "MUTDEF-001" not in _codes(result.violations)


def test_mutdef001_does_not_fire_on_tuple_default(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="def process(coords=(0, 0)):\n    return coords\n",
    )

    # Act
    result = MutableDefaultsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "MUTDEF-001" not in _codes(result.violations)
