from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import ScriptedChatModel


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENT_MODEL", "AGENT_BLOCKED_TOPICS", "AGENT_TOOL_CALL_LIMIT", "ADMIN_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client_for() -> Callable[[ScriptedChatModel], TestClient]:
    def build(model: ScriptedChatModel) -> TestClient:
        return TestClient(create_app(model))

    return build


ADMIN_KEY = "test-admin-key"
ADMIN = {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture
def admin_client_for(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[ScriptedChatModel], TestClient]:
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)

    def build(model: ScriptedChatModel) -> TestClient:
        return TestClient(create_app(model))

    return build
