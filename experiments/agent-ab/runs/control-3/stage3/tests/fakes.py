"""Fake chat models for tests (no real LLM is ever called)."""

import itertools
from collections.abc import Iterator
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import Field

_ids = itertools.count()


def tool_call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args, "id": f"call_{next(_ids)}", "type": "tool_call"}


def ai(content: str = "", *calls: dict[str, Any]) -> AIMessage:
    return AIMessage(content=content, tool_calls=list(calls))


class FakeToolModel(GenericFakeChatModel):
    """GenericFakeChatModel that accepts bind_tools and records what it was sent."""

    received: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tools: list[str] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeToolModel":
        self.bound_tools = [getattr(t, "name", None) or t.get("name") for t in tools]
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        self.received.append(list(messages))
        return super()._generate(messages, *args, **kwargs)

    @property
    def calls(self) -> int:
        return len(self.received)

    def human_texts(self) -> list[str]:
        return [m.text for msgs in self.received for m in msgs if m.type == "human"]


def scripted(*responses: AIMessage) -> FakeToolModel:
    return FakeToolModel(messages=iter(responses))


def endless(make: Any) -> FakeToolModel:
    """A model that answers every call with `make()`, forever."""

    def gen() -> Iterator[AIMessage]:
        while True:
            yield make()

    return FakeToolModel(messages=gen())
