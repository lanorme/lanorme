"""Fake chat models for driving the agent without a real LLM."""

import itertools
from collections.abc import Callable, Iterator, Sequence

from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from pydantic import Field


class ScriptedChatModel(GenericFakeChatModel):
    """A fake chat model that replays scripted messages and records its inputs."""

    received: list[list[BaseMessage]] = Field(default_factory=list)

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

    @property
    def last_user_text(self) -> str:
        return next(message.text for message in reversed(self.received[-1]) if message.type == "human")


def make_tool_call(name: str, args: dict[str, str], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def repeat_calculator_calls() -> Iterator[AIMessage]:
    """A model that asks for one more calculation on every step, forever."""
    for step in itertools.count():
        yield make_tool_call("calculator", {"expression": f"{step} + 1"}, f"call_{step}")


type ClientFactory = Callable[["ScriptedChatModel"], TestClient]
