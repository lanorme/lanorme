"""Fake chat models for driving the agent without a real LLM."""

import itertools
import json
import re
from collections.abc import Callable, Iterator, Sequence

import httpx
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from pydantic import Field


class ScriptedChatModel(GenericFakeChatModel):
    """A fake chat model that replays scripted messages and records its inputs.

    When streamed, it splits each reply into chunks of ``chunk_size`` characters
    (or at whitespace when ``chunk_size`` is ``None``) and, unlike its parent,
    keeps tool calls, sending them in a final chunk after the text.
    """

    received: list[list[BaseMessage]] = Field(default_factory=list)
    chunk_size: int | None = None

    def bind_tools(self, tools: Sequence[object], **kwargs: object) -> "ScriptedChatModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        self.received.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> Iterator[ChatGenerationChunk]:
        message = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs).generations[0].message
        for piece in split_text(message.text, size=self.chunk_size):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece, id=message.id))
            if run_manager:
                run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk
        calls = [
            tool_call_chunk(name=call["name"], args=json.dumps(call["args"]), id=call["id"], index=index)
            for index, call in enumerate(getattr(message, "tool_calls", []))
        ]
        yield ChatGenerationChunk(message=AIMessageChunk(content="", id=message.id, tool_call_chunks=calls, chunk_position="last"))

    @property
    def last_user_text(self) -> str:
        return next(message.text for message in reversed(self.received[-1]) if message.type == "human")


def split_text(text: str, *, size: int | None) -> list[str]:
    """Cut text into fixed-size pieces, or at whitespace (keeping it) when ``size`` is ``None``."""
    if size is None:
        return [piece for piece in re.split(r"(\s)", text) if piece]
    return [text[start : start + size] for start in range(0, len(text), size)]


def make_tool_call(name: str, args: dict[str, str], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def repeat_calculator_calls() -> Iterator[AIMessage]:
    """A model that asks for one more calculation on every step, forever."""
    for step in itertools.count():
        yield make_tool_call("calculator", {"expression": f"{step} + 1"}, f"call_{step}")


type ClientFactory = Callable[["ScriptedChatModel"], TestClient]

ADMIN_KEY = "test-admin-key"
DEFAULT_POLICY = {"blocked_topics": ["weapons", "malware"], "redact_pii": True, "max_tool_calls": 5, "system_prompt": None}


def make_policy(**overrides: object) -> dict[str, object]:
    """A full policy body: the default policy with some fields changed."""
    return DEFAULT_POLICY | overrides


def put_policy(*, client: TestClient, tenant_id: str, policy: dict[str, object]) -> None:
    """Store a tenant's policy through the admin API, failing loudly if it is refused."""
    response = client.put(f"/tenants/{tenant_id}/policy", json=policy, headers={"X-Admin-Key": ADMIN_KEY})
    assert response.status_code == 200, response.text


def find_system_text(model: ScriptedChatModel) -> str:
    """The system instructions the model saw on its most recent call."""
    return next(message.text for message in model.received[-1] if message.type == "system")


def read_events(response: httpx.Response) -> list[dict[str, object]]:
    """Parse a text/event-stream body, insisting on one ``data:`` line and a blank line per event."""
    body = response.text
    assert body.endswith("\n\n"), body
    events = []
    for frame in body.removesuffix("\n\n").split("\n\n"):
        assert frame.startswith("data: ") and "\n" not in frame, frame
        events.append(json.loads(frame.removeprefix("data: ")))
    return events


def join_tokens(events: list[dict[str, object]]) -> str:
    """The reply a stream spells out: its token contents, in order."""
    return "".join(str(event["content"]) for event in events if event["type"] == "token")


def list_model_turns(model: ScriptedChatModel) -> list[tuple[str, str]]:
    """The conversation the model saw on its latest call, as (type, text) pairs, without instructions."""
    return [(message.type, message.text) for message in model.received[-1] if message.type in {"human", "ai"}]
