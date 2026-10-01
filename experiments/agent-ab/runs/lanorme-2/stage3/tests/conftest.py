from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel

from app.main import create_app

type ClientFactory = Callable[[BaseChatModel], TestClient]

CONFIG_VARIABLES = ("AGENT_MODEL", "BLOCKED_TOPICS", "MAX_TOOL_CALLS", "ADMIN_API_KEY")
ADMIN_KEY = "test-admin-key"
ADMIN_HEADERS = {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in CONFIG_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def make_client() -> ClientFactory:
    def build(model: BaseChatModel) -> TestClient:
        return TestClient(create_app(model))

    return build


@pytest.fixture
def make_admin_client(monkeypatch: pytest.MonkeyPatch, make_client: ClientFactory) -> ClientFactory:
    """Like make_client, with ADMIN_API_KEY set to ADMIN_KEY before the app is built."""
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)
    return make_client
