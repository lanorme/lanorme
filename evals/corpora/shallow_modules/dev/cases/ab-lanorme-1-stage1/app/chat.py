"""One guarded chat turn: blocklist, redaction, agent call, tool-call limit."""

from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from app.guardrails.blocklist import TopicBlocklist
from app.guardrails.events import BLOCKED, REDACTED, STOPPED, GuardrailEvent
from app.guardrails.pii import redact_pii
from app.guardrails.tool_budget import ToolCallLimitExceededError, open_turn


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


class ChatService:
    """Runs a single, memoryless turn of the agent behind the guardrails."""

    def __init__(
        self, *, agent: CompiledStateGraph, blocklist: TopicBlocklist, tool_call_limit: int
    ) -> None:
        self._agent = agent
        self._blocklist = blocklist
        self._tool_call_limit = tool_call_limit

    async def reply(self, message: str) -> ChatTurn:
        """Answer ``message``, raising ``TopicBlockedError`` before any model call."""
        if self._blocklist.matches(message):
            raise TopicBlockedError
        turn = ChatTurn(reply="")
        inbound = redact_pii(message)
        if inbound.changed:
            turn.record(REDACTED)
        raw_reply = await self._run_within_budget(inbound.text)
        if raw_reply is None:
            turn.record(STOPPED)
            raw_reply = (
                f"Tool call limit reached: this turn may make at most "
                f"{self._tool_call_limit} tool calls, so it was stopped."
            )
        outbound = redact_pii(raw_reply)
        if outbound.changed:
            turn.record(REDACTED)
        turn.reply = outbound.text
        return turn

    async def _run_within_budget(self, message: str) -> str | None:
        """Run the agent on a fresh conversation; ``None`` means the budget ran out.

        A subagent's overrun surfaces inside its ``task`` tool, where the agent
        loop may turn the exception into a tool error and carry on, so the
        shared counter is checked as well as the exception.
        """
        with open_turn() as calls:
            try:
                result = await self._agent.ainvoke({"messages": [HumanMessage(content=message)]})
            except ToolCallLimitExceededError:
                return None
        return None if calls.exceeded else result["messages"][-1].text
