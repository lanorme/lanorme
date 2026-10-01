import pytest
from pydantic import ValidationError

from app.infrastructure.settings import Settings


def test_defaults() -> None:
    # When
    settings = Settings()

    # Then
    assert settings.blocked_topics == ("weapons", "malware")
    assert settings.max_tool_calls == 5
    assert settings.agent_model


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    monkeypatch.setenv("AGENT_MODEL", "openai:gpt-test")
    monkeypatch.setenv("BLOCKED_TOPICS", " gambling , ,crypto scams")
    monkeypatch.setenv("MAX_TOOL_CALLS", "2")

    # When
    settings = Settings()

    # Then
    assert settings.agent_model == "openai:gpt-test"
    assert settings.blocked_topics == ("gambling", "crypto scams")
    assert settings.max_tool_calls == 2


def test_empty_blocklist_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLOCKED_TOPICS", "")

    assert Settings().blocked_topics == ()


def test_rejects_negative_tool_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TOOL_CALLS", "-1")

    with pytest.raises(ValidationError):
        Settings()
