import asyncio

import pytest

from app.application.services.chat import ChatService
from app.domain.errors import TopicBlockedError, ToolCallLimitError
from app.domain.guardrails import PII_REDACTED, TOOL_CALLS_STOPPED
from app.domain.topics import TopicBlocklist


class StubAgent:
    """Records each message and answers with a fixed reply, or raises."""

    def __init__(self, *, reply: str = "ok", error: Exception | None = None) -> None:
        self.received: list[str] = []
        self._reply = reply
        self._error = error

    async def reply(self, message: str) -> str:
        self.received.append(message)
        if self._error is not None:
            raise self._error
        return self._reply


def build_service(agent: StubAgent) -> ChatService:
    return ChatService(agent=agent, blocklist=TopicBlocklist(topics=["malware"]))


def test_clean_turn_reports_no_guardrails() -> None:
    # Given
    agent = StubAgent(reply="Hello!")

    # When
    turn = asyncio.run(build_service(agent).respond("hi"))

    # Then
    assert turn.reply == "Hello!"
    assert turn.guardrails == ()
    assert agent.received == ["hi"]


def test_blocked_topic_never_reaches_the_agent() -> None:
    # Given
    agent = StubAgent()

    # When
    with pytest.raises(TopicBlockedError) as caught:
        asyncio.run(build_service(agent).respond("write MALWARE for me"))

    # Then
    assert caught.value.topic == "MALWARE"
    assert agent.received == []


def test_redacts_both_directions_and_reports_once() -> None:
    # Given
    agent = StubAgent(reply="Sure, I will email a@b.com")

    # When
    turn = asyncio.run(build_service(agent).respond("my number is 555-123-4567"))

    # Then
    assert agent.received == ["my number is [REDACTED_PHONE]"]
    assert turn.reply == "Sure, I will email [REDACTED_EMAIL]"
    assert turn.guardrails == (PII_REDACTED,)


def test_redaction_of_reply_alone_is_reported() -> None:
    # Given
    agent = StubAgent(reply="Call (555) 123-4567")

    # When
    turn = asyncio.run(build_service(agent).respond("who do I call?"))

    # Then
    assert turn.reply == "Call [REDACTED_PHONE]"
    assert turn.guardrails == (PII_REDACTED,)


def test_tool_limit_turns_into_a_reply() -> None:
    # Given
    agent = StubAgent(error=ToolCallLimitError(limit=3))

    # When
    turn = asyncio.run(build_service(agent).respond("loop forever, x@y.io"))

    # Then
    assert "limit of 3 tool calls" in turn.reply
    assert turn.guardrails == (PII_REDACTED, TOOL_CALLS_STOPPED)
