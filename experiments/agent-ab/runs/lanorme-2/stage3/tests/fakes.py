"""Fake chat model for driving the real deep agent without a provider."""

import json
import re
from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from pydantic import Field


class ScriptedChatModel(GenericFakeChatModel):
    """Replies from a script and records the messages it was called with.

    Streams like a provider does: the text in pieces (whitespace-separated
    tokens, or chunk_size characters at a time), then the tool calls. The
    parent's _stream drops tool calls, which streaming agent runs depend on.
    """

    calls: list[list[BaseMessage]] = Field(default_factory=list)
    chunk_size: int | None = None

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

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        message = self._generate(messages, stop=stop, **kwargs).generations[0].message
        assert isinstance(message, AIMessage)
        pieces = [
            AIMessageChunk(content=text, id=message.id) for text in self._split(message.text)
        ]
        pieces.extend(
            AIMessageChunk(
                content="",
                id=message.id,
                tool_call_chunks=[
                    {
                        "name": call["name"],
                        "args": json.dumps(call["args"]),
                        "id": call["id"],
                        "index": index,
                    }
                ],
            )
            for index, call in enumerate(message.tool_calls)
        )
        for piece in pieces:
            chunk = ChatGenerationChunk(message=piece)
            if run_manager:
                run_manager.on_llm_new_token(piece.text, chunk=chunk)
            yield chunk

    def _split(self, text: str) -> list[str]:
        if not text:
            return []
        if self.chunk_size is None:
            return [token for token in re.split(r"(\s)", text) if token]
        return [text[start : start + self.chunk_size] for start in range(0, len(text), self.chunk_size)]


def build_scripted_model(*replies: AIMessage, chunk_size: int | None = None) -> ScriptedChatModel:
    return ScriptedChatModel(messages=iter(replies), chunk_size=chunk_size)


def build_tool_call(name: str, args: dict[str, Any], call_id: str, text: str = "") -> AIMessage:
    return AIMessage(content=text, tool_calls=[{"name": name, "args": args, "id": call_id}])


def build_clock_calls(count: int) -> Iterator[AIMessage]:
    return (build_tool_call("current_time", {}, f"call-{index}") for index in range(count))
