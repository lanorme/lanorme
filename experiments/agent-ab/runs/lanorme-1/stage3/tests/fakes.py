"""A fake chat model that supports tool binding and streaming, and records its inputs."""

import json
from collections.abc import Iterator, Sequence
from typing import Any, override

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from pydantic import Field


class ScriptedChatModel(GenericFakeChatModel):
    """Replays scripted replies and keeps every prompt it was sent.

    Streamed, a reply arrives as ``chunk_size``-character pieces of text
    followed by its tool calls, as a provider would send them.
    """

    prompts: list[list[BaseMessage]] = Field(default_factory=list)
    chunk_size: int = 3

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

    @override
    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        result = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        message = result.generations[0].message
        text = message.text
        for start in range(0, len(text), self.chunk_size):
            piece = text[start : start + self.chunk_size]
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece, id=message.id))
            if run_manager:
                run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk
        calls = message.tool_calls if isinstance(message, AIMessage) else []
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                id=message.id,
                chunk_position="last",
                tool_call_chunks=[
                    {"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"],
                     "index": index, "type": "tool_call_chunk"}
                    for index, call in enumerate(calls)
                ],
            )
        )

    @property
    def calls(self) -> int:
        return len(self.prompts)


def script(*replies: AIMessage | str, chunk_size: int = 3) -> ScriptedChatModel:
    return ScriptedChatModel(messages=iter(replies), chunk_size=chunk_size)


def tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def calling(*calls: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=list(calls))


def endless_calculator_calls() -> Iterator[AIMessage]:
    index = 0
    while True:
        index += 1
        yield calling(tool_call("calculator", {"expression": "1 + 1"}, f"call-{index}"))


class ModelDownError(Exception):
    """Raised by a scripted model whose provider is down."""


def failing_after(*replies: AIMessage | str) -> ScriptedChatModel:
    def replies_then_failure() -> Iterator[AIMessage | str]:
        yield from replies
        raise ModelDownError

    return ScriptedChatModel(messages=replies_then_failure())
