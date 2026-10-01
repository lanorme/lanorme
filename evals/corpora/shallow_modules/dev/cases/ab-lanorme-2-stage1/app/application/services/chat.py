"""One guarded chat turn: blocklist, PII redaction and tool-call limit."""

from dataclasses import dataclass

from app.application.ports.chat_agent import ChatAgent
from app.domain.errors import TopicBlockedError, ToolCallLimitError
from app.domain.guardrails import PII_REDACTED, TOOL_CALLS_STOPPED, GuardrailAction
from app.domain.pii import redact_pii
from app.domain.topics import TopicBlocklist


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """The reply returned to the user and every guardrail that acted on the turn."""

    reply: str
    guardrails: tuple[GuardrailAction, ...]


class ChatService:
    """Runs a user message through the guardrails and the agent."""

    def __init__(self, *, agent: ChatAgent, blocklist: TopicBlocklist) -> None:
        self._agent = agent
        self._blocklist = blocklist

    async def respond(self, message: str) -> ChatTurn:
        """Answer one message, applying every guardrail around the agent call.

        Raises TopicBlockedError before the agent is called when the message
        mentions a blocked topic.
        """
        topic = self._blocklist.find_topic(message)
        if topic is not None:
            raise TopicBlockedError(topic=topic)

        inbound = redact_pii(message)
        stopped = False
        try:
            raw_reply = await self._agent.reply(inbound.text)
        except ToolCallLimitError as exc:
            raw_reply = _describe_limit(exc.limit)
            stopped = True
        outbound = redact_pii(raw_reply)

        actions: list[GuardrailAction] = []
        if inbound.changed or outbound.changed:
            actions.append(PII_REDACTED)
        if stopped:
            actions.append(TOOL_CALLS_STOPPED)
        return ChatTurn(reply=outbound.text, guardrails=tuple(actions))


def _describe_limit(limit: int) -> str:
    return (
        f"I had to stop: this turn reached the limit of {limit} tool calls. "
        "Please narrow the request or split it into smaller steps."
    )
