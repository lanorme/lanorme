"""One guarded chat turn under a tenant policy: blocklist, redaction, agent, tool limit."""

from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from app.agent import TurnContext
from app.guardrails.blocklist import TopicBlocklist
from app.guardrails.events import BLOCKED, REDACTED, STOPPED, GuardrailEvent
from app.guardrails.pii import redact_pii
from app.guardrails.tool_budget import ToolCallLimitExceededError, open_turn
from app.policies import TenantPolicy


class TopicBlockedError(Exception):
    """The message mentions a blocked topic; the model was not called."""

    event = BLOCKED


@dataclass(slots=True)
class ChatTurn:
    """The reply to one message and the guardrails that acted on it."""

    reply: str
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


class ChatService:
    """Runs a single, memoryless turn of the shared agent behind a tenant's guardrails."""

    def __init__(self, *, agent: CompiledStateGraph) -> None:
        self._agent = agent

    async def reply(self, *, message: str, policy: TenantPolicy) -> ChatTurn:
        """Answer ``message``, raising ``TopicBlockedError`` before any model call."""
        if TopicBlocklist(policy.blocked_topics).matches(message):
            raise TopicBlockedError
        turn = ChatTurn(reply="")
        inbound = turn.redact(text=message, policy=policy)
        raw_reply = await self._run_within_budget(message=inbound, policy=policy)
        if raw_reply is None:
            turn.record(STOPPED)
            raw_reply = (
                f"Tool call limit reached: this turn may make at most "
                f"{policy.max_tool_calls} tool calls, so it was stopped."
            )
        turn.reply = turn.redact(text=raw_reply, policy=policy)
        return turn

    async def _run_within_budget(self, *, message: str, policy: TenantPolicy) -> str | None:
        """Run the agent on a fresh conversation; ``None`` means the budget ran out.

        A subagent's overrun surfaces inside its ``task`` tool, where the agent
        loop may turn the exception into a tool error and carry on, so the
        shared counter is checked as well as the exception.
        """
        context = TurnContext(tenant_instructions=policy.system_prompt)
        with open_turn(limit=policy.max_tool_calls) as calls:
            try:
                result = await self._agent.ainvoke(
                    {"messages": [HumanMessage(content=message)]}, context=context
                )
            except ToolCallLimitExceededError:
                return None
        return None if calls.exceeded else result["messages"][-1].text
