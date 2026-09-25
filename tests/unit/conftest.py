"""Shared pytest fixtures for the LaNorme unit tests.

Centralising fixtures here keeps the per-test arrange blocks small and lets
AAA-002 (DRY tests) pass: the repeated setup lives in one place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme.scan import Scan


@pytest.fixture(autouse=True)
def _isolate_run_state(monkeypatch):
    """Give every test a fresh scan and no GitHub Actions auto-detect.

    A fresh :class:`~lanorme.scan.Scan` means no exclude glob, scope or parsed
    tree carries over from one test to the next, whatever the test set
    through the compatibility setters; the registered checks need no reset,
    since a run configures copies and never the templates.

    ``GITHUB_ACTIONS`` is cleared so the output-format auto-detect is off by
    default: the suite itself runs inside GitHub Actions, where leaving it set
    would flip the default format to ``github`` and break tests that parse the
    human or JSON output. A test that wants the auto-detect sets it explicitly.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with Scan().activate():
        yield


@pytest.fixture
def tmp_py_file(tmp_path: Path):
    """Return a callable that writes a .py file into the test's tmp_path."""

    def _write(*, name: str, body: str) -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    return _write
