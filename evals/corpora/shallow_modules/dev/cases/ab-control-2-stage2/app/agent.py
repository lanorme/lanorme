"""Builds the deep agent and runs one guarded conversational turn."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from deepagents import create_deep_agent
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware import AgentMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage

from app.config import Settings
from app.guardrails import (
    PII_REDACTION,
    TOOL_CALL_LIMIT,
    TOPIC_BLOCKLIST,
    ToolCallLimitExceeded,
    TopicBlocklist,
    TurnToolCallLimitMiddleware,
    redact_pii,
    tool_call_budget,
)
from app.policies import TenantPolicy
from app.tools import TOOLS


def build_model(settings: Settings) -> BaseChatModel:
    return init_chat_model(settings.model)


# --- Tenant instructions ------------------------------------------------------

_turn_instructions: ContextVar[str | None] = ContextVar("turn_instructions", default=None)


@contextmanager
def turn_instructions(text: str | None) -> Iterator[None]:
    """Scope extra system instructions (a tenant's system prompt) to one turn."""
    token = _turn_instructions.set(text)
    try:
        yield
    finally:
        _turn_instructions.reset(token)


class TurnInstructionsMiddleware(AgentMiddleware):
    """Appends the turn's extra instructions to the system message of every model call.

    The agent is built once and shared by all tenants, so the instructions come
    from a ContextVar set per turn rather than from the agent's own prompt.
    """

    name = "TurnInstructionsMiddleware"

    def _apply(self, request: Any) -> Any:
        extra = _turn_instructions.get()
        if not extra:
            return request
        system = request.system_message
        if system is None:
            content: str | list[Any] = extra
        elif isinstance(system.content, str):
            content = f"{system.content}\n\n{extra}"
        else:
            content = [*system.content, {"type": "text", "text": extra}]
        return request.override(system_message=SystemMessage(content=content))

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._apply(request))

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        return await handler(self._apply(request))


def build_agent(model: BaseChatModel, settings: Settings) -> Any:
    # deepagents does not pass custom middleware on to subagents, so the
    # general-purpose subagent is declared explicitly with the same middleware.
    # Both draw from the same per-turn budget (see `tool_call_budget`) and get
    # the same tenant instructions. Any subagent added here must include them too.
    def middleware() -> list[AgentMiddleware]:
        return [TurnToolCallLimitMiddleware(), TurnInstructionsMiddleware()]

    general_purpose = {**GENERAL_PURPOSE_SUBAGENT, "middleware": middleware()}
    return create_deep_agent(
        model=model,
        tools=TOOLS,
        system_prompt=settings.system_prompt,
        middleware=middleware(),
        subagents=[general_purpose],
    )


@lru_cache(maxsize=256)
def _blocklist(topics: tuple[str, ...]) -> TopicBlocklist:
    return TopicBlocklist(topics)


@dataclass
class TurnResult:
    reply: str = ""
    guardrails: list[dict[str, str]] = field(default_factory=list)
    blocked: bool = False


class GuardedAgent:
    def __init__(self, model: BaseChatModel, settings: Settings) -> None:
        self.settings = settings
        self.default_policy = TenantPolicy.default(settings)
        self.agent = build_agent(model, settings)

    async def run_turn(self, message: str, policy: TenantPolicy | None = None) -> TurnResult:
        """Run one turn under `policy` (the default policy when None)."""
        policy = self.default_policy if policy is None else policy
        result = TurnResult()

        def acted(name: str, action: str) -> None:
            entry = {"name": name, "action": action}
            if entry not in result.guardrails:
                result.guardrails.append(entry)

        if _blocklist(tuple(policy.blocked_topics)).find(message):
            acted(TOPIC_BLOCKLIST, "blocked")
            result.blocked = True
            return result

        if policy.redact_pii:
            message, redacted = redact_pii(message)
            if redacted:
                acted(PII_REDACTION, "redacted")

        limit = policy.max_tool_calls
        try:
            with tool_call_budget(limit), turn_instructions(policy.system_prompt):
                state = await self.agent.ainvoke({"messages": [{"role": "user", "content": message}]})
        except ToolCallLimitExceeded:
            acted(TOOL_CALL_LIMIT, "stopped")
            result.reply = (
                f"I stopped working on this request because it reached the limit of "
                f"{limit} tool calls per message. Please try a simpler or narrower request."
            )
            return result

        reply = _final_reply(state)
        if policy.redact_pii:
            reply, redacted = redact_pii(reply)
            if redacted:
                acted(PII_REDACTION, "redacted")
        result.reply = reply
        return result


def _final_reply(state: dict[str, Any]) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, AIMessage):
            return msg.text
    return ""
