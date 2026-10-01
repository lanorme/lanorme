import json
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from app.config import get_settings
from app.main import create_app


class Chunks(list[str]):
    """A scripted reply streamed in exactly these chunks (its content is their join)."""


class ToolCallingFakeModel(GenericFakeChatModel):
    """GenericFakeChatModel that accepts ``bind_tools`` and records its inputs.

    Each scripted response is a str, an AIMessage or ``Chunks``. When streamed, a
    str is split on whitespace (as GenericFakeChatModel does), content blocks are
    streamed one block per chunk, ``Chunks`` as given, and tool calls follow as
    tool-call chunks, so tool calls survive streaming.
    """

    seen: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ToolCallingFakeModel":
        return self

    def _next(self, messages: list[BaseMessage]) -> tuple[AIMessage, list[Any]]:
        self.seen.append(list(messages))
        response = next(self.messages)
        if isinstance(response, Chunks):
            return AIMessage(content="".join(response)), list(response)
        if isinstance(response, str):
            response = AIMessage(content=response)
        if isinstance(response.content, str):
            return response, [p for p in re.split(r"(\s)", response.content) if p]
        return response, [[block] for block in response.content]

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        message, _ = self._next(messages)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        message, parts = self._next(messages)
        chunks = [AIMessageChunk(content=part) for part in parts]
        if message.tool_calls or not chunks:
            chunks.append(
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                        for i, c in enumerate(message.tool_calls)
                    ],  # empty for an empty reply: a stream yields at least one chunk
                )
            )
        for chunk in chunks:
            generation = ChatGenerationChunk(message=chunk)
            if run_manager:
                run_manager.on_llm_new_token(chunk.text, chunk=generation)
            yield generation


def make_model(responses: Iterable[AIMessage | str | Chunks]) -> ToolCallingFakeModel:
    return ToolCallingFakeModel(messages=iter(list(responses)), seen=[])


def tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch: pytest.MonkeyPatch):
    for var in (
        "AGENT_MODEL",
        "AGENT_BLOCKED_TOPICS",
        "AGENT_MAX_TOOL_CALLS",
        "AGENT_SYSTEM_PROMPT",
        "ADMIN_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client_for() -> Callable[[BaseChatModel], TestClient]:
    def _make(model: BaseChatModel) -> TestClient:
        return TestClient(create_app(model))

    return _make
