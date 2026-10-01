"""Shared fixtures. No test ever reaches a real model."""

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.main import create_app
from tests.fakes import ADMIN_KEY, ClientFactory, ScriptedChatModel


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENT_MODEL", "BLOCKED_TOPICS", "MAX_TOOL_CALLS", "ADMIN_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client_for() -> ClientFactory:
    def build(model: ScriptedChatModel) -> TestClient:
        return TestClient(create_app(model))

    return build


@pytest.fixture
def replying_model() -> ScriptedChatModel:
    return ScriptedChatModel(messages=iter([AIMessage(content="Happy to help.")]))


@pytest.fixture
def admin_key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)
    return ADMIN_KEY
