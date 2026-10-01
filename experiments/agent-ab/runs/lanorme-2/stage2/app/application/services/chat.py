"""One guarded chat turn under the tenant's policy: blocklist, PII redaction, tool-call limit."""

from dataclasses import dataclass

from app.application.ports.chat_agent import ChatAgent
from app.application.services.policies import PolicyService
from app.domain.errors import TopicBlockedError, ToolCallLimitError
from app.domain.guardrails import PII_REDACTED, TOOL_CALLS_STOPPED, GuardrailAction
from app.domain.pii import Redaction, redact_pii
from app.domain.policy import GuardrailPolicy
from app.domain.topics import TopicBlocklist


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """The reply returned to the user and every guardrail that acted on the turn."""

    reply: str
    guardrails: tuple[GuardrailAction, ...]


class ChatService:
    """Runs a user message through the calling tenant's guardrails and the agent."""

    def __init__(self, *, agent: ChatAgent, policies: PolicyService) -> None:
        self._agent = agent
        self._policies = policies

    async def respond(self, message: str, *, tenant_id: str) -> ChatTurn:
        """Answer one message, applying every guardrail of the tenant's policy.

        Raises TopicBlockedError before the agent is called when the message
        mentions one of the tenant's blocked topics.
        """
        policy = await self._policies.get_policy(tenant_id)
        topic = TopicBlocklist(topics=policy.blocked_topics).find_topic(message)
        if topic is not None:
            raise TopicBlockedError(topic=topic)

        inbound = _redact_if(text=message, policy=policy)
        stopped = False
        try:
            raw_reply = await self._agent.reply(
                inbound.text,
                max_tool_calls=policy.max_tool_calls,
                system_prompt=policy.system_prompt,
            )
        except ToolCallLimitError as exc:
            raw_reply = _describe_limit(exc.limit)
            stopped = True
        outbound = _redact_if(text=raw_reply, policy=policy)

        actions: list[GuardrailAction] = []
        if inbound.changed or outbound.changed:
            actions.append(PII_REDACTED)
        if stopped:
            actions.append(TOOL_CALLS_STOPPED)
        return ChatTurn(reply=outbound.text, guardrails=tuple(actions))


def _redact_if(*, text: str, policy: GuardrailPolicy) -> Redaction:
    if policy.redact_pii:
        return redact_pii(text)
    return Redaction(text=text, changed=False)


def _describe_limit(limit: int) -> str:
    return (
        f"I had to stop: this turn reached the limit of {limit} tool calls. "
        "Please narrow the request or split it into smaller steps."
    )
