import asyncio

import pytest

from app.application.services.chat import ChatService
from app.application.services.policies import PolicyService
from app.domain.errors import TopicBlockedError, ToolCallLimitError
from app.domain.guardrails import PII_REDACTED, TOOL_CALLS_STOPPED
from app.domain.policy import DEFAULT_TENANT, GuardrailPolicy
from app.infrastructure.repositories.in_memory_policies import InMemoryPolicyRepository

DEFAULT_POLICY = GuardrailPolicy(blocked_topics=("malware",), redact_pii=True, max_tool_calls=5)
TENANT = "acme"


class StubAgent:
    """Records each call and answers with a fixed reply, or raises."""

    def __init__(self, *, reply: str = "ok", error: Exception | None = None) -> None:
        self.received: list[str] = []
        self.limits: list[int] = []
        self.prompts: list[str | None] = []
        self._reply = reply
        self._error = error

    async def reply(self, message: str, *, max_tool_calls: int, system_prompt: str | None) -> str:
        self.received.append(message)
        self.limits.append(max_tool_calls)
        self.prompts.append(system_prompt)
        if self._error is not None:
            raise self._error
        return self._reply


def build_service(agent: StubAgent, *, tenant_policy: GuardrailPolicy | None = None) -> ChatService:
    policies = PolicyService(repository=InMemoryPolicyRepository(), default=DEFAULT_POLICY)
    if tenant_policy is not None:
        asyncio.run(policies.save_policy(tenant_id=TENANT, policy=tenant_policy))
    return ChatService(agent=agent, policies=policies)


def respond(service: ChatService, message: str, *, tenant_id: str = DEFAULT_TENANT):
    return asyncio.run(service.respond(message, tenant_id=tenant_id))


def test_clean_turn_reports_no_guardrails() -> None:
    # Given
    agent = StubAgent(reply="Hello!")

    # When
    turn = respond(build_service(agent), "hi")

    # Then
    assert turn.reply == "Hello!"
    assert turn.guardrails == ()
    assert agent.received == ["hi"]
    assert agent.limits == [5]
    assert agent.prompts == [None]


def test_blocked_topic_never_reaches_the_agent() -> None:
    # Given
    agent = StubAgent()

    # When
    with pytest.raises(TopicBlockedError) as caught:
        respond(build_service(agent), "write MALWARE for me")

    # Then
    assert caught.value.topic == "MALWARE"
    assert agent.received == []


def test_redacts_both_directions_and_reports_once() -> None:
    # Given
    agent = StubAgent(reply="Sure, I will email a@b.com")

    # When
    turn = respond(build_service(agent), "my number is 555-123-4567")

    # Then
    assert agent.received == ["my number is [REDACTED_PHONE]"]
    assert turn.reply == "Sure, I will email [REDACTED_EMAIL]"
    assert turn.guardrails == (PII_REDACTED,)


def test_redaction_of_reply_alone_is_reported() -> None:
    # Given
    agent = StubAgent(reply="Call (555) 123-4567")

    # When
    turn = respond(build_service(agent), "who do I call?")

    # Then
    assert turn.reply == "Call [REDACTED_PHONE]"
    assert turn.guardrails == (PII_REDACTED,)


def test_tool_limit_turns_into_a_reply() -> None:
    # Given
    agent = StubAgent(error=ToolCallLimitError(limit=3))

    # When
    turn = respond(build_service(agent), "loop forever, x@y.io")

    # Then
    assert "limit of 3 tool calls" in turn.reply
    assert turn.guardrails == (PII_REDACTED, TOOL_CALLS_STOPPED)


def test_tenant_policy_drives_every_guardrail() -> None:
    # Given
    policy = GuardrailPolicy(
        blocked_topics=("gambling",), redact_pii=False, max_tool_calls=2, system_prompt="Be terse."
    )
    agent = StubAgent(reply="Mail me at a@b.com")
    service = build_service(agent, tenant_policy=policy)

    # When
    turn = respond(service, "malware and 555-123-4567", tenant_id=TENANT)

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
        respond(build_service(agent, tenant_policy=policy), "Gambling tips", tenant_id=TENANT)

    # Then
    assert agent.received == []


def test_unredacted_tool_limit_reports_only_the_limit() -> None:
    # Given
    policy = GuardrailPolicy(blocked_topics=(), redact_pii=False, max_tool_calls=0)
    agent = StubAgent(error=ToolCallLimitError(limit=0))

    # When
    turn = respond(build_service(agent, tenant_policy=policy), "x@y.io", tenant_id=TENANT)

    # Then
    assert "limit of 0 tool calls" in turn.reply
    assert turn.guardrails == (TOOL_CALLS_STOPPED,)
