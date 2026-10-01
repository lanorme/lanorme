import pytest
from pydantic import ValidationError

from app.settings import Settings


def test_defaults() -> None:
    settings = Settings()

    assert settings.blocked_topics == ("weapons", "malware")
    assert settings.tool_call_limit == 5


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    monkeypatch.setenv("AGENT_MODEL", "openai:gpt-x")
    monkeypatch.setenv("AGENT_BLOCKED_TOPICS", " drugs ,, gambling ")
    monkeypatch.setenv("AGENT_TOOL_CALL_LIMIT", "3")

    # When
    settings = Settings()

    # Then
    assert settings.model == "openai:gpt-x"
    assert settings.blocked_topics == ("drugs", "gambling")
    assert settings.tool_call_limit == 3


def test_rejects_negative_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TOOL_CALL_LIMIT", "-1")

    with pytest.raises(ValidationError):
        Settings()
