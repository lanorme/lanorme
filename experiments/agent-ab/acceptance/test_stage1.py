"""Stage 1 contract: health, chat, PII redaction, topic blocklist, tool-call limit."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fakes import ScriptedChatModel, read_human_texts
from support import BLOCKED_BODY, LIMIT_WORDS, PII, TOOL_LIMIT, find_guardrails, build_id, post_chat

PHONES = ["+44 20 7946 0958", "(555) 123-4567", "555-123-4567"]
EMAILS = ["ada@example.com", "grace.hopper@mail.example.org"]


def find_redacted_message(model: ScriptedChatModel, placeholder: str) -> str:
    """Return the human message of the turn's first model call holding `placeholder`."""
    assert model.calls, "the model was never called"
    matches = [text for text in read_human_texts(model.calls[0]) if placeholder in text]
    assert matches, f"no user message the model saw contains {placeholder}"
    return matches[-1]


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chat_returns_reply_shape(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    model.add_replies("The capital of France is Paris.")
    session = build_id("s")
    # Act
    response = post_chat(client, message="What is the capital of France?", session=session)
    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session
    assert isinstance(body["reply"], str)
    assert "The capital of France is Paris." in body["reply"]
    assert body["guardrails"] == []


def test_model_sees_the_user_message(client: TestClient, model: ScriptedChatModel) -> None:
    post_chat(client, message="Tell me a fact about otters.")
    assert any("Tell me a fact about otters." in text for text in read_human_texts(model.calls[0]))


@pytest.mark.parametrize("email", EMAILS)
def test_input_email_is_redacted_before_the_model(
    client: TestClient,
    model: ScriptedChatModel,
    email: str,
) -> None:
    # Act
    response = post_chat(client, message=f"My email is {email}, thanks.")
    # Assert
    assert response.status_code == 200
    seen = find_redacted_message(model, "[REDACTED_EMAIL]")
    assert email not in seen
    assert "@" not in seen
    assert PII in find_guardrails(response.json())


@pytest.mark.parametrize("phone", PHONES)
def test_input_phone_is_redacted_before_the_model(
    client: TestClient,
    model: ScriptedChatModel,
    phone: str,
) -> None:
    # Act
    response = post_chat(client, message=f"Call me on {phone} please.")
    # Assert
    assert response.status_code == 200
    seen = find_redacted_message(model, "[REDACTED_PHONE]")
    assert phone not in seen
    assert not any(char.isdigit() for char in seen), f"phone digits leaked to the model: {seen!r}"
    assert PII in find_guardrails(response.json())


def test_raw_pii_never_reaches_the_model(client: TestClient, model: ScriptedChatModel) -> None:
    post_chat(client, message="Reach ada@example.com or 555-123-4567.")
    for call in model.calls:
        for text in read_human_texts(call):
            assert "ada@example.com" not in text
            assert "555-123-4567" not in text


@pytest.mark.parametrize("email", EMAILS)
def test_output_email_is_redacted(client: TestClient, model: ScriptedChatModel, email: str) -> None:
    # Arrange
    model.add_replies(f"You can write to {email} for details.")
    # Act
    response = post_chat(client, message="Who should I write to?")
    # Assert
    assert response.status_code == 200
    body = response.json()
    assert "[REDACTED_EMAIL]" in body["reply"]
    assert email not in body["reply"]
    assert "@" not in body["reply"]
    assert PII in find_guardrails(body)


@pytest.mark.parametrize("phone", PHONES)
def test_output_phone_is_redacted(client: TestClient, model: ScriptedChatModel, phone: str) -> None:
    # Arrange
    model.add_replies(f"Ring {phone} any time.")
    # Act
    response = post_chat(client, message="What number should I ring?")
    # Assert
    assert response.status_code == 200
    body = response.json()
    assert "[REDACTED_PHONE]" in body["reply"]
    assert not any(char.isdigit() for char in body["reply"]), body["reply"]
    assert PII in find_guardrails(body)


def test_clean_turn_lists_no_guardrails(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    model.add_replies("Otters hold hands while they sleep.")
    # Act
    response = post_chat(client, message="Tell me about otters.")
    # Assert
    assert response.status_code == 200
    assert response.json()["guardrails"] == []


def test_guardrail_entries_have_name_and_action(client: TestClient) -> None:
    body = post_chat(client, message="Mail me at ada@example.com").json()
    assert body["guardrails"], "PII redaction acted but no guardrail was listed"
    for entry in body["guardrails"]:
        assert isinstance(entry["name"], str)
        assert isinstance(entry["action"], str)


@pytest.mark.parametrize(
    "message",
    [
        "Tell me about weapons",
        "Where can I buy WEAPONS?",
        "How does Malware spread?",
        "weapons.",
        "I study malware, weapons, and more",
    ],
)
def test_blocked_topic_returns_403_without_calling_the_model(
    client: TestClient,
    model: ScriptedChatModel,
    message: str,
) -> None:
    # Act
    response = post_chat(client, message=message)
    # Assert
    assert response.status_code == 403
    body = response.json()
    assert {key: body.get(key) for key in BLOCKED_BODY} == BLOCKED_BODY
    assert model.calls == [], "the model was called for a blocked message"


@pytest.mark.parametrize(
    "message",
    ["I met a weaponsmith at the fair.", "Is antimalware software useful?"],
)
def test_blocklist_matches_whole_words_only(
    client: TestClient,
    model: ScriptedChatModel,
    message: str,
) -> None:
    response = post_chat(client, message=message)
    assert response.status_code == 200
    assert model.calls, "the model was not called for an allowed message"


def test_tool_call_limit_stops_the_turn(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    model.loop_tool_calls()
    # Act
    response = post_chat(client, message="Plan my week in as many steps as you like.")
    # Assert
    assert response.status_code == 200
    body = response.json()
    assert TOOL_LIMIT in find_guardrails(body)
    assert isinstance(body["reply"], str)
    assert LIMIT_WORDS.search(body["reply"]), (
        f"the reply does not say the limit was reached: {body['reply']!r}"
    )


def test_tool_call_limit_defaults_to_five(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    model.loop_tool_calls()
    # Act
    response = post_chat(client, message="Plan my week.")
    # Assert
    assert response.status_code == 200
    # Five tool calls take five model calls; the call that asks for a sixth may
    # or may not be counted before the turn stops, so allow a little slack.
    assert 5 <= len(model.calls) <= 7, f"the model was called {len(model.calls)} times"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"session_id": "s-1"},
        {"message": "hello"},
        {"session_id": "s-1", "message": None},
        {"session_id": None, "message": "hello"},
    ],
)
def test_malformed_body_returns_422(
    client: TestClient,
    model: ScriptedChatModel,
    body: dict,
) -> None:
    response = client.post("/chat", json=body)
    assert response.status_code == 422
    assert model.calls == []


def test_non_json_body_returns_422(client: TestClient) -> None:
    response = client.post(
        "/chat",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
