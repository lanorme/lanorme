"""Builds the deepagents agent and runs one guarded turn through it."""

import threading
from collections.abc import Awaitable, Callable
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
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph.state import CompiledStateGraph

from app.config import Settings
from app.guardrails import (
    PII_EVENT,
    TOOL_LIMIT_EVENT,
    GuardrailEvent,
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


def build_model(settings: Settings) -> BaseChatModel:
    """Create the real chat model from configuration. Only called at app startup."""
    from langchain.chat_models import init_chat_model

    return init_chat_model(settings.model)


def _message_text(message: AIMessage) -> str:
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

    async def run_turn(self, message: str, policy: TenantPolicy) -> TurnResult:
        if TopicBlocklist(policy.blocked_topics).matches(message):
            raise BlockedTopicError
        events: list[GuardrailEvent] = []

        if policy.redact_pii:
            safe_message, redacted_in = redact_pii(message)
        else:
            safe_message, redacted_in = message, False
        try:
            state = await self.graph_for(policy.max_tool_calls).ainvoke(
                {"messages": [HumanMessage(content=safe_message)]},
                context=TurnContext(tenant_system_prompt=policy.system_prompt),
            )
        except ToolCallLimitExceededError:
            reply = TOOL_LIMIT_REPLY.format(limit=policy.max_tool_calls)
            events.append(TOOL_LIMIT_EVENT)
            redacted_out = False
        else:
            last = next(
                (m for m in reversed(state["messages"]) if isinstance(m, AIMessage)),
                None,
            )
            text = _message_text(last) if last else ""
            if policy.redact_pii:
                reply, redacted_out = redact_pii(text)
            else:
                reply, redacted_out = text, False

        if redacted_in or redacted_out:
            events.insert(0, PII_EVENT)
        return TurnResult(reply=reply, guardrails=events)
