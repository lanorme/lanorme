"""Unit tests for the EXC-001 swallowed-exceptions check."""

from __future__ import annotations

from lanorme.checks.swallowed_exceptions import SwallowedExceptionsCheck


def _codes(violations) -> set[str]:
    return {v.rule for v in violations}


def test_exc001_fires_on_pass_only(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body=(
            "def load(path):\n"
            "    try:\n"
            "        return open(path).read()\n"
            "    except OSError:\n"
            "        pass\n"
        ),
    )

    # Act
    result = SwallowedExceptionsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "EXC-001" in _codes(result.violations)


def test_exc001_fires_on_bare_except_pass(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body=(
            "def connect():\n"
            "    try:\n"
            "        return do_connect()\n"
            "    except:\n"
            "        pass\n"
        ),
    )

    # Act
    result = SwallowedExceptionsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "EXC-001" in _codes(result.violations)


def test_exc001_does_not_fire_when_reraised(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body=(
            "def load(path):\n"
            "    try:\n"
            "        return open(path).read()\n"
            "    except OSError:\n"
            "        cleanup()\n"
            "        raise\n"
        ),
    )

    # Act
    result = SwallowedExceptionsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "EXC-001" not in _codes(result.violations)


def test_exc001_does_not_fire_when_logged(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body=(
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "\n"
            "def load(path):\n"
            "    try:\n"
            "        return open(path).read()\n"
            "    except OSError as exc:\n"
            "        logger.error('failed to load %s', path, exc_info=exc)\n"
        ),
    )

    # Act
    result = SwallowedExceptionsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "EXC-001" not in _codes(result.violations)


def test_exc001_does_not_fire_when_raised_from(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body=(
            "class AppError(Exception):\n"
            "    pass\n"
            "\n"
            "def load(path):\n"
            "    try:\n"
            "        return open(path).read()\n"
            "    except OSError as exc:\n"
            "        raise AppError('cannot load') from exc\n"
        ),
    )

    # Act
    result = SwallowedExceptionsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "EXC-001" not in _codes(result.violations)
