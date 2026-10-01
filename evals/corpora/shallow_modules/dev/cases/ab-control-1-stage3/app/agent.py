"""Builds the deepagents agent and runs one guarded turn through it."""

import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallLimitMiddleware,
)
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph.state import CompiledStateGraph

from app.config import Settings
from app.conversations import StoredMessage
from app.guardrails import (
    PII_EVENT,
    TOOL_LIMIT_EVENT,
    GuardrailEvent,
    StreamingRedactor,
    TopicBlocklist,
    redact_pii,
)
from app.policies import TenantPolicy
from app.tools import CUSTOM_TOOLS

TOOL_LIMIT_REPLY = (
    "I stopped working on this request because it reached the limit of "
    "{limit} tool calls per turn."
)


class BlockedTopicError(Exception):
    """Raised when a message hits the topic blocklist; the model is never called."""


@dataclass
class TurnResult:
    reply: str
    guardrails: list[GuardrailEvent] = field(default_factory=list)
    # The user's message as it should be stored: redacted where the policy redacts.
    message: str = ""

    def to_store(self) -> list[StoredMessage]:
        return [
            StoredMessage(role="user", content=self.message),
            StoredMessage(role="assistant", content=self.reply),
        ]


@dataclass
class _PreparedTurn:
    policy: TenantPolicy
    safe_message: str
    redacted_in: bool
    graph_input: dict[str, Any]
    context: "TurnContext"


def build_model(settings: Settings) -> BaseChatModel:
    """Create the real chat model from configuration. Only called at app startup."""
    from langchain.chat_models import init_chat_model

    return init_chat_model(settings.model)


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    # Content blocks (e.g. Anthropic): keep the text parts.
    return "".join(
        block if isinstance(block, str) else block.get("text", "")
        for block in content
        if isinstance(block, str) or block.get("type") == "text"
    )


@dataclass(frozen=True)
class TurnContext:
    """Per-invoke runtime context, readable by middleware as ``runtime.context``."""

    tenant_system_prompt: str | None = None


def _append_instructions(system: SystemMessage | None, extra: str) -> SystemMessage:
    if system is None:
        return SystemMessage(content=extra)
    if isinstance(system.content, str):
        return SystemMessage(content=f"{system.content}\n\n{extra}")
    return SystemMessage(content=[*system.content, {"type": "text", "text": f"\n\n{extra}"}])


class TenantInstructionsMiddleware(AgentMiddleware):
    """Appends the calling tenant's system prompt to the agent's instructions."""

    def _with_tenant_prompt(self, request: ModelRequest) -> ModelRequest:
        context = request.runtime.context if request.runtime else None
        extra = getattr(context, "tenant_system_prompt", None)
        if not extra:
            return request
        return request.override(system_message=_append_instructions(request.system_message, extra))

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        return handler(self._with_tenant_prompt(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._with_tenant_prompt(request))


class GuardedAgent:
    def __init__(self, model: BaseChatModel, settings: Settings) -> None:
        self.model = model
        self.settings = settings
        # ToolCallLimitMiddleware fixes its limit at construction, so keep one
        # compiled graph per limit in use. The set of limits is small and only
        # changes through the admin API.
        self._graphs: dict[int, CompiledStateGraph[Any, Any, Any, Any]] = {}
        self._graphs_lock = threading.Lock()

    def graph_for(self, max_tool_calls: int) -> CompiledStateGraph[Any, Any, Any, Any]:
        with self._graphs_lock:
            graph = self._graphs.get(max_tool_calls)
            if graph is None:
                graph = self._graphs[max_tool_calls] = self._build_graph(max_tool_calls)
            return graph

    def _build_graph(self, max_tool_calls: int) -> CompiledStateGraph[Any, Any, Any, Any]:
        return create_deep_agent(
            model=self.model,
            tools=CUSTOM_TOOLS,
            system_prompt=self.settings.system_prompt,
            context_schema=TurnContext,
            middleware=[
                # Counts every tool call the main agent makes within one invoke.
                # "error" aborts the run before any over-limit tool executes.
                ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior="error"),
                TenantInstructionsMiddleware(),
            ],
        )

    def _prepare(
        self, message: str, policy: TenantPolicy, history: Sequence[StoredMessage]
    ) -> _PreparedTurn:
        if TopicBlocklist(policy.blocked_topics).matches(message):
            raise BlockedTopicError
        if policy.redact_pii:
            safe_message, redacted_in = redact_pii(message)
        else:
            safe_message, redacted_in = message, False
        return _PreparedTurn(
            policy=policy,
            safe_message=safe_message,
            redacted_in=redacted_in,
            graph_input={
                "messages": [*_history_messages(history, policy), HumanMessage(content=safe_message)]
            },
            context=TurnContext(tenant_system_prompt=policy.system_prompt),
        )

    def _result(
        self, turn: _PreparedTurn, reply: str, redacted_out: bool, limited: bool
    ) -> TurnResult:
        events: list[GuardrailEvent] = []
        if turn.redacted_in or redacted_out:
            events.append(PII_EVENT)
        if limited:
            events.append(TOOL_LIMIT_EVENT)
        return TurnResult(reply=reply, guardrails=events, message=turn.safe_message)

    async def run_turn(
        self, message: str, policy: TenantPolicy, history: Sequence[StoredMessage] = ()
    ) -> TurnResult:
        """Run one turn after ``history`` (the conversation so far, as stored)."""
        turn = self._prepare(message, policy, history)
        try:
            state = await self.graph_for(policy.max_tool_calls).ainvoke(
                turn.graph_input, context=turn.context
            )
        except ToolCallLimitExceededError:
            reply = TOOL_LIMIT_REPLY.format(limit=policy.max_tool_calls)
            return self._result(turn, reply, redacted_out=False, limited=True)
        text = _final_text(state)
        if policy.redact_pii:
            reply, redacted_out = redact_pii(text)
        else:
            reply, redacted_out = text, False
        return self._result(turn, reply, redacted_out, limited=False)

    def stream_turn(
        self, message: str, policy: TenantPolicy, history: Sequence[StoredMessage] = ()
    ) -> AsyncIterator[str | TurnResult]:
        """Like ``run_turn``, but yield the reply as text chunks, then the TurnResult.

        The guardrail checks run eagerly, so a blocked message raises
        ``BlockedTopicError`` here, before anything is streamed. The chunks join to
        exactly ``TurnResult.reply`` (and so to what ``run_turn`` would reply).

        Only the agent's final message is the reply; a message that ends in tool
        calls is not, and that is known only once the message is complete. So the
        model's chunks are collected per message and released, with the model's
        own chunk boundaries, once the final message is known.
        """
        turn = self._prepare(message, policy, history)
        return self._stream(turn)

    async def _stream(self, turn: _PreparedTurn) -> AsyncIterator[str | TurnResult]:
        policy = turn.policy
        chunks: dict[str | None, list[str]] = {}
        state: dict[str, Any] = {}
        try:
            async for mode, data in self.graph_for(policy.max_tool_calls).astream(
                turn.graph_input, context=turn.context, stream_mode=["messages", "values"]
            ):
                if mode == "messages":
                    chunk, _metadata = data
                    if isinstance(chunk, AIMessage):  # AIMessageChunk subclasses AIMessage
                        chunks.setdefault(chunk.id, []).append(_message_text(chunk))
                else:
                    state = data
        except ToolCallLimitExceededError:
            reply = TOOL_LIMIT_REPLY.format(limit=policy.max_tool_calls)
            yield reply
            yield self._result(turn, reply, redacted_out=False, limited=True)
            return

        last = _final_message(state)
        text = _message_text(last) if last else ""
        pieces = [c for c in chunks.get(last.id if last else None, []) if c]
        if "".join(pieces) != text:
            # The streamed chunks don't add up to the final message (e.g. the
            # model didn't stream); fall back to the whole text in one piece.
            pieces = [text] if text else []

        if policy.redact_pii:
            redactor = StreamingRedactor()
            out = [redactor.feed(p) for p in pieces] + [redactor.finish()]
            redacted_out = redactor.redacted
        else:
            out, redacted_out = pieces, False
        out = [o for o in out if o]
        for piece in out:
            yield piece
        yield self._result(turn, "".join(out), redacted_out, limited=False)


def _history_messages(history: Sequence[StoredMessage], policy: TenantPolicy) -> list[BaseMessage]:
    """The stored conversation as model input.

    Stored content was redacted under the policy in force when it was stored;
    if the tenant redacts now, redact again so PII stored while redaction was
    off never reaches the model. (Redacting redacted text changes nothing.)
    """
    messages: list[BaseMessage] = []
    for stored in history:
        content = redact_pii(stored.content)[0] if policy.redact_pii else stored.content
        cls = HumanMessage if stored.role == "user" else AIMessage
        messages.append(cls(content=content))
    return messages


def _final_message(state: dict[str, Any]) -> AIMessage | None:
    return next(
        (m for m in reversed(state.get("messages", [])) if isinstance(m, AIMessage)),
        None,
    )


def _final_text(state: dict[str, Any]) -> str:
    last = _final_message(state)
    return _message_text(last) if last else ""
