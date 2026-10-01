"""Builds the deepagents agent and runs one guarded turn through it."""

from dataclasses import dataclass, field

from deepagents import create_deep_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.config import Settings
from app.guardrails import (
    PII_EVENT,
    TOOL_LIMIT_EVENT,
    GuardrailEvent,
    TopicBlocklist,
    redact_pii,
)
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


class GuardedAgent:
    def __init__(self, model: BaseChatModel, settings: Settings) -> None:
        self.settings = settings
        self.blocklist = TopicBlocklist(settings.blocked_topics)
        self.graph = create_deep_agent(
            model=model,
            tools=CUSTOM_TOOLS,
            system_prompt=settings.system_prompt,
            middleware=[
                # Counts every tool call the main agent makes within one invoke.
                # "error" aborts the run before any over-limit tool executes.
                ToolCallLimitMiddleware(
                    run_limit=settings.max_tool_calls, exit_behavior="error"
                ),
            ],
        )

    async def run_turn(self, message: str) -> TurnResult:
        if self.blocklist.matches(message):
            raise BlockedTopicError
        events: list[GuardrailEvent] = []

        safe_message, redacted_in = redact_pii(message)
        try:
            state = await self.graph.ainvoke(
                {"messages": [HumanMessage(content=safe_message)]}
            )
        except ToolCallLimitExceededError:
            reply = TOOL_LIMIT_REPLY.format(limit=self.settings.max_tool_calls)
            events.append(TOOL_LIMIT_EVENT)
            redacted_out = False
        else:
            last = next(
                (m for m in reversed(state["messages"]) if isinstance(m, AIMessage)),
                None,
            )
            reply, redacted_out = redact_pii(_message_text(last) if last else "")

        if redacted_in or redacted_out:
            events.insert(0, PII_EVENT)
        return TurnResult(reply=reply, guardrails=events)
