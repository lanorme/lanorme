import asyncio
from collections.abc import AsyncIterator, Sequence

import pytest

from app.application.services.chat import ChatService, ChatTurn, ReplyChunk, TurnEvent
from app.application.services.policies import PolicyService
from app.domain.conversation import ChatMessage, Role, SessionKey
from app.domain.errors import AgentUnavailableError, TopicBlockedError, ToolCallLimitError
from app.domain.guardrails import PII_REDACTED, TOOL_CALLS_STOPPED
from app.domain.policy import DEFAULT_TENANT, GuardrailPolicy
from app.infrastructure.repositories.in_memory_conversations import InMemoryConversationRepository
from app.infrastructure.repositories.in_memory_policies import InMemoryPolicyRepository

DEFAULT_POLICY = GuardrailPolicy(blocked_topics=("malware",), redact_pii=True, max_tool_calls=5)
TENANT = "acme"
KEY = SessionKey(tenant_id=DEFAULT_TENANT, session_id="s-1")
TENANT_KEY = SessionKey(tenant_id=TENANT, session_id="s-1")


class StubAgent:
    """Records each call and streams fixed replies chunk by chunk, then raises if told to."""

    def __init__(self, *replies: Sequence[str], error: Exception | None = None) -> None:
        self.received: list[str] = []
        self.histories: list[tuple[ChatMessage, ...]] = []
        self.limits: list[int] = []
        self.prompts: list[str | None] = []
        self._replies = list(replies) or [["ok"]]
        self._error = error

    async def stream_reply(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage],
        max_tool_calls: int,
        system_prompt: str | None,
    ) -> AsyncIterator[str]:
        self.received.append(message)
        self.histories.append(tuple(history))
        self.limits.append(max_tool_calls)
        self.prompts.append(system_prompt)
        for chunk in self._replies[min(len(self.received), len(self._replies)) - 1]:
            yield chunk
        if self._error is not None:
            raise self._error


class Harness:
    """A ChatService on in-memory stores, with the conversation store exposed."""

    def __init__(self, agent: StubAgent, *, tenant_policy: GuardrailPolicy | None = None) -> None:
        policies = PolicyService(repository=InMemoryPolicyRepository(), default=DEFAULT_POLICY)
        if tenant_policy is not None:
            asyncio.run(policies.save_policy(tenant_id=TENANT, policy=tenant_policy))
        self.policies = policies
        self.conversations = InMemoryConversationRepository()
        self.service = ChatService(agent=agent, policies=policies, conversations=self.conversations)

    def respond(self, message: str, *, key: SessionKey = KEY) -> ChatTurn:
        return asyncio.run(self.service.respond(message, key=key))

    def stream(self, message: str, *, key: SessionKey = KEY) -> list[TurnEvent]:
        async def collect() -> list[TurnEvent]:
            return [event async for event in await self.service.start_turn(message, key=key)]

        return asyncio.run(collect())

    def read_stored(self, key: SessionKey = KEY) -> list[tuple[str, str]] | None:
        messages = asyncio.run(self.conversations.list_messages(key))
        return None if messages is None else [(item.role.value, item.content) for item in messages]


def test_clean_turn_reports_no_guardrails() -> None:
    # Given
    agent = StubAgent(["Hello!"])

    # When
    turn = Harness(agent).respond("hi")

    # Then
    assert turn.reply == "Hello!"
    assert turn.guardrails == ()
    assert agent.received == ["hi"]
    assert agent.limits == [5]
    assert agent.prompts == [None]


def test_blocked_topic_never_reaches_the_agent_and_is_not_stored() -> None:
    # Given
    agent = StubAgent()
    harness = Harness(agent)

    # When
    with pytest.raises(TopicBlockedError) as caught:
        harness.respond("write MALWARE for me")

    # Then
    assert caught.value.topic == "MALWARE"
    assert agent.received == []
    assert harness.read_stored() is None


def test_blocked_topic_is_refused_before_a_stream_starts() -> None:
    # Given
    harness = Harness(StubAgent())

    # When
    with pytest.raises(TopicBlockedError):
        asyncio.run(harness.service.start_turn("malware", key=KEY))

    # Then
    assert harness.read_stored() is None


def test_redacts_both_directions_and_reports_once() -> None:
    # Given
    agent = StubAgent(["Sure, I will email a@b.com"])

    # When
    turn = Harness(agent).respond("my number is 555-123-4567")

    # Then
    assert agent.received == ["my number is [REDACTED_PHONE]"]
    assert turn.reply == "Sure, I will email [REDACTED_EMAIL]"
    assert turn.guardrails == (PII_REDACTED,)


def test_redaction_of_reply_alone_is_reported() -> None:
    # Given
    agent = StubAgent(["Call (555) 123-4567"])

    # When
    turn = Harness(agent).respond("who do I call?")

    # Then
    assert turn.reply == "Call [REDACTED_PHONE]"
    assert turn.guardrails == (PII_REDACTED,)


def test_pii_split_across_chunks_never_leaves_unredacted() -> None:
    # Given
    agent = StubAgent(["Mail jo", "e@exa", "mple.com or ring 555-1", "23-4567 today"])

    # When
    events = Harness(agent).stream("contact?")

    # Then
    chunks = [event.text for event in events if isinstance(event, ReplyChunk)]
    assert "".join(chunks) == "Mail [REDACTED_EMAIL] or ring [REDACTED_PHONE] today"
    assert not any("@" in chunk or "555" in chunk for chunk in chunks)
    assert events[-1] == ChatTurn(reply="".join(chunks), guardrails=(PII_REDACTED,))


def test_stream_ends_with_the_turn_that_respond_returns() -> None:
    # Given
    replies = (["It ", "is ", "noon."], ["It ", "is ", "noon."])
    harness = Harness(StubAgent(*replies))

    # When
    events = harness.stream("time?")
    whole = harness.respond("time?")

    # Then
    assert events[:-1] == [ReplyChunk("It "), ReplyChunk("is "), ReplyChunk("noon.")]
    assert events[-1] == whole


def test_tool_limit_turns_into_a_reply() -> None:
    # Given
    agent = StubAgent([], error=ToolCallLimitError(limit=3))

    # When
    turn = Harness(agent).respond("loop forever, x@y.io")

    # Then
    assert turn.reply.startswith("I had to stop: this turn reached the limit of 3 tool calls.")
    assert turn.guardrails == (PII_REDACTED, TOOL_CALLS_STOPPED)


def test_tool_limit_notice_follows_text_already_streamed() -> None:
    # Given
    agent = StubAgent(["Checking the clock."], error=ToolCallLimitError(limit=1))

    # When
    turn = Harness(agent).respond("time?")

    # Then
    assert turn.reply.startswith("Checking the clock.\n\nI had to stop:")


def test_tenant_policy_drives_every_guardrail() -> None:
    # Given
    policy = GuardrailPolicy(
        blocked_topics=("gambling",), redact_pii=False, max_tool_calls=2, system_prompt="Be terse."
    )
    agent = StubAgent(["Mail me at a@b.com"])

    # When
    turn = Harness(agent, tenant_policy=policy).respond("malware and 555-123-4567", key=TENANT_KEY)

    # Then
    assert agent.received == ["malware and 555-123-4567"]
    assert agent.limits == [2]
    assert agent.prompts == ["Be terse."]
    assert turn.reply == "Mail me at a@b.com"
    assert turn.guardrails == ()


def test_tenant_blocklist_replaces_the_default_one() -> None:
    # Given
    policy = GuardrailPolicy(blocked_topics=("gambling",), redact_pii=True, max_tool_calls=5)
    agent = StubAgent()

    # When
    with pytest.raises(TopicBlockedError):
        Harness(agent, tenant_policy=policy).respond("Gambling tips", key=TENANT_KEY)

    # Then
    assert agent.received == []


def test_unredacted_tool_limit_reports_only_the_limit() -> None:
    # Given
    policy = GuardrailPolicy(blocked_topics=(), redact_pii=False, max_tool_calls=0)
    agent = StubAgent([], error=ToolCallLimitError(limit=0))

    # When
    turn = Harness(agent, tenant_policy=policy).respond("x@y.io", key=TENANT_KEY)

    # Then
    assert "limit of 0 tool calls" in turn.reply
    assert turn.guardrails == (TOOL_CALLS_STOPPED,)


def test_turns_are_stored_redacted_and_sent_back_as_history() -> None:
    # Given
    agent = StubAgent(["Noted, a@b.com."], ["You said so."])
    harness = Harness(agent)

    # When
    harness.respond("I am 555-123-4567")
    harness.respond("What did I say?")

    # Then
    first_turn = [
        ("user", "I am [REDACTED_PHONE]"),
        ("assistant", "Noted, [REDACTED_EMAIL]."),
    ]
    assert [(item.role.value, item.content) for item in agent.histories[1]] == first_turn
    assert harness.read_stored() == [*first_turn, ("user", "What did I say?"), ("assistant", "You said so.")]


def test_sessions_are_scoped_to_the_tenant() -> None:
    # Given
    agent = StubAgent(["one"], ["two"])
    harness = Harness(agent)

    # When
    harness.respond("first", key=KEY)
    harness.respond("second", key=TENANT_KEY)

    # Then
    assert agent.histories[1] == ()
    assert harness.read_stored(TENANT_KEY) == [("user", "second"), ("assistant", "two")]


def test_failed_turn_is_not_stored() -> None:
    # Given
    harness = Harness(StubAgent(["partial "], error=AgentUnavailableError()))

    # When
    with pytest.raises(AgentUnavailableError):
        harness.respond("hello")

    # Then
    assert harness.read_stored() is None


def test_history_is_redacted_again_when_the_policy_has_since_turned_redaction_on() -> None:
    # Given
    agent = StubAgent(["ok"], ["ok"])
    harness = Harness(
        agent, tenant_policy=GuardrailPolicy(blocked_topics=(), redact_pii=False, max_tool_calls=5)
    )
    harness.respond("I am a@b.com", key=TENANT_KEY)
    asyncio.run(harness.policies.reset_policy(TENANT))

    # When
    turn = harness.respond("again", key=TENANT_KEY)

    # Then
    assert agent.histories[1][0] == ChatMessage(role=Role.USER, content="I am [REDACTED_EMAIL]")
    assert turn.guardrails == (PII_REDACTED,)
