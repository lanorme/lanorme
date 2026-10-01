"""A scripted fake chat model for the agent A/B acceptance suite.

`ScriptedChatModel` stands in for a real LLM behind any implementation of the
contract. It records every list of messages it is invoked with, answers from a
queue of scripted replies, can loop on tool calls forever, and streams each
reply in chunks the script controls.
"""

from __future__ import annotations

import itertools
import json
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, override

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import PrivateAttr

DEFAULT_REPLY = "Happy to help with that."
TOOL_LOOP_CAP = 50
TOOL_LOOP_GIVE_UP = "Still planning, giving up now."
TODO_ARGS = {"todos": [{"content": "Keep planning", "status": "pending"}]}


@dataclass(frozen=True)
class ScriptedToolCall:
    """A `write_todos` tool call the model emits in its tool loop."""

    call_id: str
    name: str = "write_todos"


@dataclass(frozen=True)
class Reply:
    """A scripted text reply, streamed as `chunks` when they are given."""

    text: str
    chunks: tuple[str, ...] = ()

    def build_chunks(self) -> list[str]:
        """Return the stream chunks: the scripted ones, or word-sized pieces."""
        if self.chunks:
            return list(self.chunks)
        pieces = self.text.split(" ")
        return [piece + " " for piece in pieces[:-1]] + [pieces[-1]]

    @classmethod
    def split(cls, *chunks: str) -> Reply:
        """Build a reply streamed as exactly these chunks; its text is their join."""
        return cls(text="".join(chunks), chunks=chunks)


class ScriptedChatModel(BaseChatModel):
    """A fake chat model that records its inputs and replies from a script.

    `bind_tools` returns the model itself, so `create_deep_agent` accepts it.
    Tests script it after the app is built; the model is shared by reference.
    """

    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)
    _script: list[Reply] = PrivateAttr(default_factory=list)
    _tool_loop: bool = PrivateAttr(default=False)
    _ids: Any = PrivateAttr(default_factory=itertools.count)
    _loop_calls: Any = PrivateAttr(default_factory=itertools.count)
    _lock: Any = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    @property
    def calls(self) -> list[list[BaseMessage]]:
        """Every list of messages the model was invoked with, in order."""
        return self._calls

    def add_replies(self, *replies: str | Reply) -> None:
        """Queue replies, consumed one per model call; then `DEFAULT_REPLY`."""
        self._script.extend(r if isinstance(r, Reply) else Reply(r) for r in replies)

    def loop_tool_calls(self, enabled: bool = True) -> None:
        """Answer every call with a `write_todos` tool call, forever.

        "Forever" is capped at `TOOL_LOOP_CAP` calls in one loop, after which
        the model gives up with a plain reply, so an implementation without a
        limit fails its test instead of spinning to the recursion limit.
        """
        self._tool_loop = enabled
        self._loop_calls = itertools.count()

    @override
    def bind_tools(self, tools: Sequence[object], **kwargs: object) -> ScriptedChatModel:
        return self

    def _take_turn(self, messages: list[BaseMessage]) -> Reply | ScriptedToolCall:
        with self._lock:
            self._calls.append(list(messages))
            if self._tool_loop and next(self._loop_calls) < TOOL_LOOP_CAP:
                return ScriptedToolCall(call_id=f"call_{next(self._ids)}")
            if self._tool_loop:
                return Reply(TOOL_LOOP_GIVE_UP)
            if self._script:
                return self._script.pop(0)
            return Reply(DEFAULT_REPLY)

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        turn = self._take_turn(messages)
        if isinstance(turn, ScriptedToolCall):
            tool_call = {
                "name": turn.name,
                "args": TODO_ARGS,
                "id": turn.call_id,
                "type": "tool_call",
            }
            message = AIMessage(content="", tool_calls=[tool_call])
        else:
            message = AIMessage(content=turn.text)
        return ChatResult(generations=[ChatGeneration(message=message)])

    @override
    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> Iterator[ChatGenerationChunk]:
        turn = self._take_turn(messages)
        if isinstance(turn, ScriptedToolCall):
            tool_chunk = {
                "name": turn.name,
                "args": json.dumps(TODO_ARGS),
                "id": turn.call_id,
                "index": 0,
                "type": "tool_call_chunk",
            }
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[tool_chunk],
                    chunk_position="last",
                ),
            )
            return
        pieces = turn.build_chunks()
        for position, piece in enumerate(pieces):
            last = position == len(pieces) - 1
            chunk = AIMessageChunk(content=piece, chunk_position="last" if last else None)
            yield ChatGenerationChunk(message=chunk)


def read_text(message: BaseMessage) -> str:
    """Return a message's text, whether its content is a string or blocks."""
    content = message.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def read_call_text(call: list[BaseMessage]) -> str:
    """Return the text of every message in one model call, joined by newlines."""
    return "\n".join(read_text(message) for message in call)


def read_human_texts(call: list[BaseMessage]) -> list[str]:
    """Return the text of each human (user) message in one model call."""
    return [read_text(message) for message in call if message.type == "human"]
