"""The deep agent and the guarded chat turn built around it."""

from dataclasses import dataclass
from functools import lru_cache, partial

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
    Redaction,
    TopicBlocklist,
    redact_pii,
)
from app.policies import GuardrailPolicy
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
TENANT_INSTRUCTIONS_HEADING = "Additional instructions for this deployment:"
# Each distinct (tool-call limit, system prompt) pair compiles its own agent;
# tenants sharing a policy shape share one, and the least used are dropped.
AGENT_CACHE_SIZE = 128


class TopicBlockedError(Exception):
    """The message mentions a blocked topic, so the model was never called."""


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """The reply to one message, redacted if the policy asks, and the guardrails that acted on it."""

    reply: str
    guardrails: list[GuardrailAction]


def build_model(settings: Settings) -> BaseChatModel:
    """Create the real chat model named by configuration."""
    return init_chat_model(settings.model_name)


def build_agent(*, model: BaseChatModel, max_tool_calls: int, tenant_prompt: str | None = None) -> CompiledStateGraph:
    """Assemble the deep agent with our tools and a per-run tool-call budget.

    ``exit_behavior="error"`` aborts the run as soon as the model asks for a
    call past the budget, so no further tool runs and the caller decides what
    to tell the user. A ``tenant_prompt`` is appended to our own instructions,
    never substituted for them.
    """
    return create_deep_agent(
        model=model,
        tools=list_custom_tools(),
        system_prompt=compose_system_prompt(tenant_prompt),
        middleware=[ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior="error")],
    )


def compose_system_prompt(tenant_prompt: str | None) -> str:
    """Our instructions, followed by the tenant's when it has any."""
    if tenant_prompt is None or not tenant_prompt.strip():
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\n\n{TENANT_INSTRUCTIONS_HEADING}\n{tenant_prompt}"


def redact_if(text: str, *, enabled: bool) -> Redaction:
    """Redact PII when the policy asks for it, else pass the text through untouched."""
    return redact_pii(text) if enabled else Redaction(text=text, changed=False)


class GuardedChat:
    """Runs one chat turn through the blocklist, PII redaction and the agent.

    The guardrails come from the policy passed with each turn. Every turn is
    independent: the agent is invoked with the current message only and keeps
    no state between calls.
    """

    def __init__(self, *, model: BaseChatModel) -> None:
        self._agent_for = lru_cache(maxsize=AGENT_CACHE_SIZE)(partial(build_agent, model=model))

    async def run_turn(self, message: str, *, policy: GuardrailPolicy) -> ChatTurn:
        """Answer one message, or raise ``TopicBlockedError`` before any model call."""
        if TopicBlocklist(policy.blocked_topics).is_blocked(message):
            raise TopicBlockedError
        request = redact_if(message, enabled=policy.redact_pii)
        guardrails: list[GuardrailAction] = []
        agent = self._agent_for(max_tool_calls=policy.max_tool_calls, tenant_prompt=policy.system_prompt)
        try:
            raw_reply = await run_agent(agent=agent, message=request.text)
        except ToolCallLimitExceededError:
            raw_reply = LIMIT_REACHED_REPLY.format(limit=policy.max_tool_calls)
            guardrails.append(GuardrailAction(name=TOOL_CALL_LIMIT, action=STOPPED))
        reply = redact_if(raw_reply, enabled=policy.redact_pii)
        if request.changed or reply.changed:
            guardrails.insert(0, GuardrailAction(name=PII_REDACTION, action=REDACTED))
        return ChatTurn(reply=reply.text, guardrails=guardrails)


async def run_agent(*, agent: CompiledStateGraph, message: str) -> str:
    """Invoke the agent and return the text of its final answer."""
    state = await agent.ainvoke({"messages": [HumanMessage(content=message)]})
    answers = [entry for entry in state["messages"] if isinstance(entry, AIMessage)]
    if not answers:
        return EMPTY_REPLY
    return answers[-1].text or EMPTY_REPLY
