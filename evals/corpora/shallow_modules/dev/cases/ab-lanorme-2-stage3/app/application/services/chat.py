"""One guarded chat turn under the tenant's policy: blocklist, PII redaction, tool-call limit.

A turn continues the session's conversation and is produced as a stream of
reply chunks, so a streamed and a whole reply come from the same code.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from app.application.ports.chat_agent import PARAGRAPH_BREAK, ChatAgent
from app.application.ports.conversation_repository import ConversationRepository
from app.application.services.policies import PolicyService
from app.domain.conversation import ChatMessage, Role, SessionKey
from app.domain.errors import TopicBlockedError, ToolCallLimitError
from app.domain.guardrails import PII_REDACTED, TOOL_CALLS_STOPPED, GuardrailAction
from app.domain.pii import Redaction, StreamingRedactor, redact_pii
from app.domain.policy import GuardrailPolicy
from app.domain.topics import TopicBlocklist


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """The reply returned to the user and every guardrail that acted on the turn."""

    reply: str
    guardrails: tuple[GuardrailAction, ...]


@dataclass(frozen=True, slots=True)
class ReplyChunk:
    """A piece of the reply, already redacted, ready to send."""

    text: str


type TurnEvent = ReplyChunk | ChatTurn


@dataclass(frozen=True, slots=True)
class _TurnInput:
    key: SessionKey
    policy: GuardrailPolicy
    inbound: Redaction


class ChatService:
    """Runs a user message through the calling tenant's guardrails and the agent."""

    def __init__(
        self, *, agent: ChatAgent, policies: PolicyService, conversations: ConversationRepository
    ) -> None:
        self._agent = agent
        self._policies = policies
        self._conversations = conversations

    async def start_turn(self, message: str, *, key: SessionKey) -> AsyncIterator[TurnEvent]:
        """Check the message, then return the turn's events: reply chunks, then one ChatTurn.

        Raises TopicBlockedError here, before the agent is called or anything
        is stored, when the message mentions one of the tenant's blocked
        topics. Iterating the events raises AgentUnavailableError when the
        model fails; the turn is stored only once its reply is complete.
        """
        policy = await self._policies.get_policy(key.tenant_id)
        topic = TopicBlocklist(topics=policy.blocked_topics).find_topic(message)
        if topic is not None:
            raise TopicBlockedError(topic=topic)
        inbound = _redact_if(text=message, policy=policy)
        return self._run_turn(_TurnInput(key=key, policy=policy, inbound=inbound))

    async def respond(self, message: str, *, key: SessionKey) -> ChatTurn:
        """Answer one message as a whole, with the same guardrails and errors as start_turn."""
        async for event in await self.start_turn(message, key=key):
            if isinstance(event, ChatTurn):
                return event
        raise RuntimeError("a turn always ends with its ChatTurn")

    async def _run_turn(self, turn: _TurnInput) -> AsyncIterator[TurnEvent]:
        stored = await self._conversations.list_messages(turn.key) or ()
        history = _redact_history(stored, policy=turn.policy)
        raw = _RawReply(
            self._agent.stream_reply(
                turn.inbound.text,
                history=history.messages,
                max_tool_calls=turn.policy.max_tool_calls,
                system_prompt=turn.policy.system_prompt,
            )
        )
        redactor = StreamingRedactor(enabled=turn.policy.redact_pii)
        sent: list[str] = []
        async for chunk in raw:
            if ready := redactor.redact_chunk(chunk):
                sent.append(ready)
                yield ReplyChunk(ready)
        if tail := redactor.flush():
            sent.append(tail)
            yield ReplyChunk(tail)

        reply = "".join(sent)
        await self._store_turn(turn, reply=reply)
        actions: list[GuardrailAction] = []
        if turn.inbound.changed or history.changed or redactor.changed:
            actions.append(PII_REDACTED)
        if raw.stopped:
            actions.append(TOOL_CALLS_STOPPED)
        yield ChatTurn(reply=reply, guardrails=tuple(actions))

    async def _store_turn(self, turn: _TurnInput, *, reply: str) -> None:
        await self._conversations.create_messages(
            key=turn.key,
            messages=(
                ChatMessage(role=Role.USER, content=turn.inbound.text),
                ChatMessage(role=Role.ASSISTANT, content=reply),
            ),
        )


class _RawReply:
    """The agent's reply before redaction; going past the tool-call limit ends it with a notice."""

    def __init__(self, chunks: AsyncIterator[str]) -> None:
        self._chunks = chunks
        self.stopped = False

    async def __aiter__(self) -> AsyncIterator[str]:
        """Yield the agent's chunks, then the limit notice if the agent was stopped."""
        agent_wrote = False
        try:
            async for chunk in self._chunks:
                agent_wrote = agent_wrote or bool(chunk)
                yield chunk
        except ToolCallLimitError as exc:
            self.stopped = True
            notice = _describe_limit(exc.limit)
            yield f"{PARAGRAPH_BREAK}{notice}" if agent_wrote else notice


@dataclass(frozen=True, slots=True)
class _History:
    messages: tuple[ChatMessage, ...]
    changed: bool


def _redact_history(messages: Sequence[ChatMessage], *, policy: GuardrailPolicy) -> _History:
    # Stored turns were redacted under the policy of their day; redacting again
    # under today's keeps PII from the model if the tenant has since turned it on.
    redacted = [(message, _redact_if(text=message.content, policy=policy)) for message in messages]
    return _History(
        messages=tuple(
            ChatMessage(role=message.role, content=redaction.text) for message, redaction in redacted
        ),
        changed=any(redaction.changed for _, redaction in redacted),
    )


def _redact_if(*, text: str, policy: GuardrailPolicy) -> Redaction:
    if policy.redact_pii:
        return redact_pii(text)
    return Redaction(text=text, changed=False)


def _describe_limit(limit: int) -> str:
    return (
        f"I had to stop: this turn reached the limit of {limit} tool calls. "
        "Please narrow the request or split it into smaller steps."
    )
