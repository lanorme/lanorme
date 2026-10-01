from collections.abc import Callable, Iterable, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult

from app.config import get_settings
from app.main import create_app


class ToolCallingFakeModel(GenericFakeChatModel):
    """GenericFakeChatModel that accepts ``bind_tools`` and records its inputs."""

    seen: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ToolCallingFakeModel":
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def make_model(responses: Iterable[AIMessage | str]) -> ToolCallingFakeModel:
    return ToolCallingFakeModel(messages=iter(list(responses)), seen=[])


def tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch: pytest.MonkeyPatch):
    for var in ("AGENT_MODEL", "AGENT_BLOCKED_TOPICS", "AGENT_MAX_TOOL_CALLS"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client_for() -> Callable[[BaseChatModel], TestClient]:
    def _make(model: BaseChatModel) -> TestClient:
        return TestClient(create_app(model))

    return _make
