"""Fixtures: a fresh scripted model and an app client for every test.

The app is built through the contract's seam only, `app.main.create_app`, with
`ADMIN_API_KEY` set before the app is created.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from fakes import ScriptedChatModel
from support import ADMIN_KEY


@pytest.fixture
def model() -> ScriptedChatModel:
    """A fresh scripted fake model; script it before sending requests."""
    return ScriptedChatModel()


@pytest.fixture
def client(model: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A test client on an app built around `model`, lifespan included."""
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)
    from app.main import create_app

    app = create_app(model=model)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
