from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import Field

from app.main import create_app

_ids = itertools.count()


class ScriptedChatModel(GenericFakeChatModel):
    """Fake chat model that replays scripted replies, supports `bind_tools`
    and records the messages it was called with."""

    calls: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedChatModel:
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        self.calls.append(list(messages))
        return super()._generate(messages, *args, **kwargs)

    def human_texts(self) -> list[str]:
        return [m.text for call in self.calls for m in call if isinstance(m, HumanMessage)]


def scripted(*replies: AIMessage | str) -> ScriptedChatModel:
    return ScriptedChatModel(messages=iter(replies))


def tool_call(name: str, **args: Any) -> AIMessage:
    return tool_calls((name, args))


def tool_calls(*calls: tuple[str, dict[str, Any]]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"call_{next(_ids)}"} for name, args in calls],
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("AGENT_MODEL", "BLOCKED_TOPICS", "MAX_TOOL_CALLS", "AGENT_SYSTEM_PROMPT", "ADMIN_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def make_client() -> Callable[..., TestClient]:
    def factory(model: ScriptedChatModel) -> TestClient:
        return TestClient(create_app(model=model))

    return factory


def chat(client: TestClient, message: str, session_id: str = "s1") -> Any:
    return client.post("/chat", json={"session_id": session_id, "message": message})
