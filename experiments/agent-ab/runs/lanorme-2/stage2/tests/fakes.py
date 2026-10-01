"""Fake chat model for driving the real deep agent without a provider."""

from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from pydantic import Field


class ScriptedChatModel(GenericFakeChatModel):
    """Replies from a script and records the messages it was called with."""

    calls: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def build_scripted_model(*replies: AIMessage) -> ScriptedChatModel:
    return ScriptedChatModel(messages=iter(replies))


def build_tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def build_clock_calls(count: int) -> Iterator[AIMessage]:
    return (build_tool_call("current_time", {}, f"call-{index}") for index in range(count))
