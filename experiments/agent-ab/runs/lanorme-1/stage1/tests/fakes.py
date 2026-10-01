"""A fake chat model that supports tool binding and records its inputs."""

from collections.abc import Iterator, Sequence
from typing import Any, override

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from pydantic import Field


class ScriptedChatModel(GenericFakeChatModel):
    """Replays scripted replies and keeps every prompt it was sent."""

    prompts: list[list[BaseMessage]] = Field(default_factory=list)

    @override
    def bind_tools(
        self, tools: Sequence[Any], **kwargs: Any
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.prompts.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    @property
    def calls(self) -> int:
        return len(self.prompts)


def script(*replies: AIMessage | str) -> ScriptedChatModel:
    return ScriptedChatModel(messages=iter(replies))


def tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def calling(*calls: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=list(calls))


def endless_calculator_calls() -> Iterator[AIMessage]:
    index = 0
    while True:
        index += 1
        yield calling(tool_call("calculator", {"expression": "1 + 1"}, f"call-{index}"))
