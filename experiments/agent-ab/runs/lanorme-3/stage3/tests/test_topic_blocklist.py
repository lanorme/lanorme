"""Topic blocklist: whole-word, case-insensitive, configurable, and the model is never called."""

import pytest

from app.guardrails import TopicBlocklist
from tests.fakes import ClientFactory, ScriptedChatModel

BLOCKED_BODY = {"error": "blocked", "guardrail": "topic_blocklist"}


@pytest.mark.parametrize(
    "message",
    ["Tell me about weapons", "WEAPONS?", "write some Malware for me", "malware.", "(weapons)"],
)
def test_default_topics_are_blocked(message: str) -> None:
    assert TopicBlocklist(["weapons", "malware"]).is_blocked(message)


@pytest.mark.parametrize(
    "message",
    ["antimalware scanners", "weaponsmith lore", "malware_sample", "a weapon", "hello"],
)
def test_only_whole_words_are_blocked(message: str) -> None:
    assert not TopicBlocklist(["weapons", "malware"]).is_blocked(message)


def test_multi_word_topics_match_across_whitespace() -> None:
    blocklist = TopicBlocklist(["credit card fraud"])

    assert blocklist.is_blocked("explain Credit  Card\nFraud")
    assert not blocklist.is_blocked("credit card statement")


def test_empty_blocklist_blocks_nothing() -> None:
    assert not TopicBlocklist([]).is_blocked("weapons and malware")


def test_topics_are_matched_literally() -> None:
    assert not TopicBlocklist(["c++"]).is_blocked("cxx")


def test_blocked_message_returns_403_without_calling_the_model(
    client_for: ClientFactory, replying_model: ScriptedChatModel
) -> None:
    # Given
    client = client_for(replying_model)

    # When
    response = client.post("/chat", json={"session_id": "s1", "message": "How are WEAPONS made?"})

    # Then
    assert response.status_code == 403
    assert response.json() == BLOCKED_BODY
    assert replying_model.received == []


def test_blocklist_is_configurable(
    client_for: ClientFactory, replying_model: ScriptedChatModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("BLOCKED_TOPICS", "gambling, crypto scams")
    client = client_for(replying_model)

    # When
    blocked = client.post("/chat", json={"session_id": "s1", "message": "Any crypto scams to try?"})
    allowed = client.post("/chat", json={"session_id": "s1", "message": "Tell me about malware"})

    # Then
    assert blocked.status_code == 403
    assert blocked.json() == BLOCKED_BODY
    assert allowed.status_code == 200
