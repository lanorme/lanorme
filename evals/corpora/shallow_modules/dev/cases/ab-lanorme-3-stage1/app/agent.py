"""The deep agent and the guarded chat turn built around it."""

from dataclasses import dataclass

from deepagents import create_deep_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph

from app.config import Settings
from app.guardrails import (
    PII_REDACTION,
    REDACTED,
    STOPPED,
    TOOL_CALL_LIMIT,
    GuardrailAction,
    TopicBlocklist,
    redact_pii,
)
from app.tools import list_custom_tools

SYSTEM_PROMPT = (
    "You are a helpful assistant. Use the calculator tool for arithmetic and the "
    "current_utc_time tool when the date or time matters. Placeholders such as "
    "[REDACTED_EMAIL] and [REDACTED_PHONE] stand for personal data the user shared; "
    "never ask for the original values."
)
LIMIT_REACHED_REPLY = (
    "I stopped working on this request because it reached the limit of {limit} tool "
    "calls per turn. Please narrow the request and try again."
)
EMPTY_REPLY = "I do not have a reply for that."


class TopicBlockedError(Exception):
    """The message mentions a blocked topic, so the model was never called."""


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """The redacted reply to one message and the guardrails that acted on it."""

    reply: str
    guardrails: list[GuardrailAction]


def build_model(settings: Settings) -> BaseChatModel:
    """Create the real chat model named by configuration."""
    return init_chat_model(settings.model_name)


def build_agent(*, model: BaseChatModel, max_tool_calls: int) -> CompiledStateGraph:
    """Assemble the deep agent with our tools and a per-run tool-call budget.

    ``exit_behavior="error"`` aborts the run as soon as the model asks for a
    call past the budget, so no further tool runs and the caller decides what
    to tell the user.
    """
    return create_deep_agent(
        model=model,
        tools=list_custom_tools(),
        system_prompt=SYSTEM_PROMPT,
        middleware=[ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior="error")],
    )


class GuardedChat:
    """Runs one chat turn through the blocklist, PII redaction and the agent.

    Every turn is independent: the agent is invoked with the current message
    only and keeps no state between calls.
    """

    def __init__(self, *, model: BaseChatModel, settings: Settings) -> None:
        self._agent = build_agent(model=model, max_tool_calls=settings.max_tool_calls)
        self._blocklist = TopicBlocklist(settings.blocked_topics)
        self._max_tool_calls = settings.max_tool_calls

    async def run_turn(self, message: str) -> ChatTurn:
        """Answer one message, or raise ``TopicBlockedError`` before any model call."""
        if self._blocklist.is_blocked(message):
            raise TopicBlockedError
        request = redact_pii(message)
        guardrails: list[GuardrailAction] = []
        try:
            raw_reply = await self._run_agent(request.text)
        except ToolCallLimitExceededError:
            raw_reply = LIMIT_REACHED_REPLY.format(limit=self._max_tool_calls)
            guardrails.append(GuardrailAction(name=TOOL_CALL_LIMIT, action=STOPPED))
        reply = redact_pii(raw_reply)
        if request.changed or reply.changed:
            guardrails.insert(0, GuardrailAction(name=PII_REDACTION, action=REDACTED))
        return ChatTurn(reply=reply.text, guardrails=guardrails)

    async def _run_agent(self, message: str) -> str:
        """Invoke the agent and return the text of its final answer."""
        state = await self._agent.ainvoke({"messages": [HumanMessage(content=message)]})
        answers = [entry for entry in state["messages"] if isinstance(entry, AIMessage)]
        if not answers:
            return EMPTY_REPLY
        return answers[-1].text or EMPTY_REPLY
