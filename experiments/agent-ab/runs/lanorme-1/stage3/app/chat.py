"""One guarded chat turn under a tenant policy: blocklist, memory, redaction, agent, tool limit.

Every turn streams. ``POST /chat`` drains the same stream ``POST /chat/stream``
forwards, so the two endpoints cannot disagree about a reply.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import TypedDict

from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph

from app.agent import TurnContext
from app.conversations import ConversationStore, Role, SessionKey, StoredMessage
from app.guardrails.blocklist import TopicBlocklist
from app.guardrails.events import BLOCKED, REDACTED, STOPPED, GuardrailEvent
from app.guardrails.pii import StreamRedactor, redact_pii
from app.guardrails.tool_budget import ToolCallLimitExceededError, open_turn
from app.policies import TenantPolicy

# The node create_agent runs the model in; other nodes stream tool results.
_MODEL_NODE = "model"
# Separates what the agent wrote in successive model calls of one turn.
_PARAGRAPH_BREAK = "\n\n"


class TopicBlockedError(Exception):
    """The message mentions a blocked topic; the model was not called."""

    event = BLOCKED


@dataclass(slots=True)
class ChatTurn:
    """The reply to one message and the guardrails that acted on it."""

    reply: str = ""
    guardrails: list[GuardrailEvent] = field(default_factory=list)

    def record(self, event: GuardrailEvent) -> None:
        """Note that ``event`` happened, once per guardrail per turn."""
        if event not in self.guardrails:
            self.guardrails.append(event)

    def redact(self, *, text: str, policy: TenantPolicy) -> str:
        """Return ``text`` with PII redacted when ``policy`` asks for it, noting any change."""
        if not policy.redact_pii:
            return text
        redaction = redact_pii(text)
        if redaction.changed:
            self.record(REDACTED)
        return redaction.text


@dataclass(slots=True)
class ReplyStream:
    """A turn in progress: its reply arrives through ``chunks``.

    ``turn`` is complete, and the exchange stored, once ``chunks`` is
    exhausted. A stream abandoned early stores nothing.
    """

    turn: ChatTurn
    chunks: AsyncIterator[str]


@dataclass(frozen=True, slots=True)
class _Exchange:
    """What a turn in progress needs: where it is stored and what the model is sent."""

    key: SessionKey
    inbound: str
    prompt: list[AnyMessage]
    policy: TenantPolicy
    turn: ChatTurn


class _StreamMetadata(TypedDict, total=False):
    """The parts of LangGraph's per-message stream metadata this module reads."""

    langgraph_node: str
    langgraph_checkpoint_ns: str


@dataclass(slots=True)
class _Paragraphs:
    """Joins the text of successive model calls with a paragraph break."""

    source: str | None = None
    wrote: bool = False

    def continue_with(self, *, source: str, text: str) -> str:
        """Return ``text``, preceded by a break when it starts a new model call's output."""
        prefix = _PARAGRAPH_BREAK if self.wrote and source != self.source else ""
        self.source, self.wrote = source, True
        return prefix + text


class ChatService:
    """Runs turns of the shared agent behind a tenant's guardrails, remembering each session."""

    def __init__(self, *, agent: CompiledStateGraph, conversations: ConversationStore) -> None:
        self._agent = agent
        self._conversations = conversations

    async def reply(self, *, key: SessionKey, message: str, policy: TenantPolicy) -> ChatTurn:
        """Answer ``message`` in one go, raising ``TopicBlockedError`` before any model call."""
        stream = await self.start(key=key, message=message, policy=policy)
        async for _ in stream.chunks:
            pass
        return stream.turn

    async def start(self, *, key: SessionKey, message: str, policy: TenantPolicy) -> ReplyStream:
        """Begin answering ``message`` within its session.

        The blocklist is checked here, before anything streams, so a refused
        message raises ``TopicBlockedError`` and is neither sent nor stored.
        """
        if TopicBlocklist(policy.blocked_topics).matches(message):
            raise TopicBlockedError
        turn = ChatTurn()
        inbound = turn.redact(text=message, policy=policy)
        history = await self._conversations.load(key) or ()
        prompt = [*_as_model_messages(history=history, policy=policy), HumanMessage(inbound)]
        exchange = _Exchange(key=key, inbound=inbound, prompt=prompt, policy=policy, turn=turn)
        return ReplyStream(turn=turn, chunks=self._stream_turn(exchange))

    async def _stream_turn(self, exchange: _Exchange) -> AsyncIterator[str]:
        """Yield the redacted reply as it is written, then store the exchange."""
        turn = exchange.turn
        redactor = StreamRedactor() if exchange.policy.redact_pii else None
        pieces: list[str] = []
        raw = self._stream_agent(exchange)
        async with aclosing(_iterate_in_own_task(raw)) as texts:
            async for text in texts:
                chunk = redactor.feed(text) if redactor else text
                if chunk:
                    pieces.append(chunk)
                    yield chunk
        if redactor is not None:
            if tail := redactor.flush():
                pieces.append(tail)
                yield tail
            if redactor.changed:
                turn.record(REDACTED)
        turn.reply = "".join(pieces)
        await self._conversations.append(
            key=exchange.key,
            messages=(
                StoredMessage(role=Role.USER, content=exchange.inbound),
                StoredMessage(role=Role.ASSISTANT, content=turn.reply),
            ),
        )

    async def _stream_agent(self, exchange: _Exchange) -> AsyncIterator[str]:
        """Yield the main agent's unredacted text as the model writes it.

        Once the tool-call budget runs out, the run stops and the reply ends
        with a notice instead. A subagent's overrun surfaces inside its
        ``task`` tool, where the agent loop may turn the exception into a tool
        error and carry on, so the shared counter is checked as well as the
        exception, before each streamed event.
        """
        policy = exchange.policy
        context = TurnContext(tenant_instructions=policy.system_prompt)
        paragraphs = _Paragraphs()
        with open_turn(limit=policy.max_tool_calls) as calls:
            run = self._agent.astream(
                {"messages": exchange.prompt}, context=context, stream_mode="messages"
            )
            try:
                async with aclosing(run) as events:
                    async for message, metadata in events:
                        if calls.exceeded:
                            break
                        if text := _main_agent_text(message=message, metadata=metadata):
                            source = metadata.get("langgraph_checkpoint_ns", "")
                            yield paragraphs.continue_with(source=source, text=text)
            except ToolCallLimitExceededError:
                pass
        if calls.exceeded:
            exchange.turn.record(STOPPED)
            notice = (
                f"Tool call limit reached: this turn may make at most "
                f"{policy.max_tool_calls} tool calls, so it was stopped."
            )
            yield paragraphs.continue_with(source="", text=notice)


def _main_agent_text(*, message: BaseMessage, metadata: _StreamMetadata) -> str:
    """Return the text of a streamed model message from the main agent, else ``""``.

    Tool results and anything a subagent writes (its namespace is nested,
    joined with ``|``) are for the agent, not the user.
    """
    namespace = metadata.get("langgraph_checkpoint_ns")
    if (
        not isinstance(message, AIMessage)
        or metadata.get("langgraph_node") != _MODEL_NODE
        or not isinstance(namespace, str)
        or "|" in namespace
    ):
        return ""
    return message.text


def _as_model_messages(
    *, history: Sequence[StoredMessage], policy: TenantPolicy
) -> list[AnyMessage]:
    """Turn stored history into model messages, redacted under the current policy.

    History is stored as redacted when it was written; redacting it again
    covers a tenant that has switched redaction on since.
    """

    def content(stored: StoredMessage) -> str:
        return redact_pii(stored.content).text if policy.redact_pii else stored.content

    return [
        HumanMessage(content(stored)) if stored.role is Role.USER else AIMessage(content(stored))
        for stored in history
    ]


async def _iterate_in_own_task[T](source: AsyncIterator[T]) -> AsyncIterator[T]:
    """Yield what ``source`` yields, running it in a task of its own.

    The turn's tool budget lives in a context variable. An async generator
    runs in its consumer's context, which a streaming response may not keep
    stable between items; a task has a context of its own for its whole life.
    Abandoning this iterator cancels ``source``.
    """
    queue: asyncio.Queue[tuple[T] | Exception | None] = asyncio.Queue(maxsize=1)

    async def pump() -> None:
        try:
            async for item in source:
                await queue.put((item,))
        except Exception as error:
            # Hand the failure to the consumer, which raises it in its own task.
            await queue.put(error)
            return
        await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while (entry := await queue.get()) is not None:
            if isinstance(entry, Exception):
                raise entry
            yield entry[0]
    finally:
        task.cancel()
        await asyncio.wait([task])
