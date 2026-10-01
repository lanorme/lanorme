"""Builds the deep agent and runs one guarded conversational turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from deepagents import create_deep_agent
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

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
from app.tools import TOOLS


def build_model(settings: Settings) -> BaseChatModel:
    return init_chat_model(settings.model)


def build_agent(model: BaseChatModel, settings: Settings) -> Any:
    # deepagents does not pass custom middleware on to subagents, so the
    # general-purpose subagent is declared explicitly with the limiter too. Both
    # draw from the same per-turn budget (see `tool_call_budget`). Any subagent
    # added here must include the limiter as well.
    general_purpose = {**GENERAL_PURPOSE_SUBAGENT, "middleware": [TurnToolCallLimitMiddleware()]}
    return create_deep_agent(
        model=model,
        tools=TOOLS,
        system_prompt=settings.system_prompt,
        middleware=[TurnToolCallLimitMiddleware()],
        subagents=[general_purpose],
    )


@dataclass
class TurnResult:
    reply: str = ""
    guardrails: list[dict[str, str]] = field(default_factory=list)
    blocked: bool = False


class GuardedAgent:
    def __init__(self, model: BaseChatModel, settings: Settings) -> None:
        self.settings = settings
        self.blocklist = TopicBlocklist(settings.blocked_topics)
        self.agent = build_agent(model, settings)

    async def run_turn(self, message: str) -> TurnResult:
        result = TurnResult()

        def acted(name: str, action: str) -> None:
            entry = {"name": name, "action": action}
            if entry not in result.guardrails:
                result.guardrails.append(entry)

        if self.blocklist.find(message):
            acted(TOPIC_BLOCKLIST, "blocked")
            result.blocked = True
            return result

        message, redacted = redact_pii(message)
        if redacted:
            acted(PII_REDACTION, "redacted")

        limit = self.settings.max_tool_calls
        try:
            with tool_call_budget(limit):
                state = await self.agent.ainvoke({"messages": [{"role": "user", "content": message}]})
        except ToolCallLimitExceeded:
            acted(TOOL_CALL_LIMIT, "stopped")
            result.reply = (
                f"I stopped working on this request because it reached the limit of "
                f"{limit} tool calls per message. Please try a simpler or narrower request."
            )
            return result

        reply, redacted = redact_pii(_final_reply(state))
        if redacted:
            acted(PII_REDACTION, "redacted")
        result.reply = reply
        return result


def _final_reply(state: dict[str, Any]) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, AIMessage):
            return msg.text
    return ""
