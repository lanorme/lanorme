"""Shared pytest fixtures for the LaNorme unit tests.

Centralising fixtures here keeps the per-test arrange blocks small and lets
AAA-002 (DRY tests) pass: the repeated setup lives in one place.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_py_file(tmp_path: Path):
    """Return a callable that writes a .py file into the test's tmp_path."""

    def _write(*, name: str, body: str) -> Path:
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        return path

    return _write
