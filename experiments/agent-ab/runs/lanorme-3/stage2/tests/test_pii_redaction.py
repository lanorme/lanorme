"""PII redaction: the regexes, and that the model and the caller only see placeholders."""

import pytest
from langchain_core.messages import AIMessage

from app.guardrails import redact_pii
from tests.fakes import ClientFactory, ScriptedChatModel

PII_ACTION = {"name": "pii_redaction", "action": "redacted"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mail jane.doe@example.com now", "mail [REDACTED_EMAIL] now"),
        ("Jane.Doe+tag@mail.example.co.uk", "[REDACTED_EMAIL]"),
        ("call +44 20 7946 0958 today", "call [REDACTED_PHONE] today"),
        ("call (555) 123-4567", "call [REDACTED_PHONE]"),
        ("call 555-123-4567.", "call [REDACTED_PHONE]."),
        ("call 555.123.4567", "call [REDACTED_PHONE]"),
        ("call +1 555 123 4567", "call [REDACTED_PHONE]"),
        ("call +1-555-123-4567", "call [REDACTED_PHONE]"),
        ("call 020 7946 0958", "call [REDACTED_PHONE]"),
        ("call +33 1 23 45 67 89", "call [REDACTED_PHONE]"),
        ("a@b.io or 555-123-4567", "[REDACTED_EMAIL] or [REDACTED_PHONE]"),
    ],
)
def test_redacts_emails_and_phone_numbers(text: str, expected: str) -> None:
    result = redact_pii(text)

    assert result.text == expected
    assert result.changed


@pytest.mark.parametrize(
    "text",
    [
        "The meeting is on 2024-01-15 at 10:30.",
        "It costs 1,299.99 and weighs 12.5 kg.",
        "Order 4521 shipped; version 3.12.1 is out.",
        "Room 101, extension 4567.",
        "Use the @ sign or email me later.",
    ],
)
def test_leaves_ordinary_text_alone(text: str) -> None:
    result = redact_pii(text)

    assert result.text == text
    assert not result.changed


def test_model_only_sees_redacted_message(client_for: ClientFactory, replying_model: ScriptedChatModel) -> None:
    # Given
    client = client_for(replying_model)
    message = "I am jane@example.com, ring me on (555) 123-4567"

    # When
    response = client.post("/chat", json={"session_id": "s1", "message": message})

    # Then
    assert replying_model.last_user_text == "I am [REDACTED_EMAIL], ring me on [REDACTED_PHONE]"
    assert response.json()["guardrails"] == [PII_ACTION]
    assert "jane@example.com" not in str(replying_model.received)


def test_reply_is_redacted_before_it_is_returned(client_for: ClientFactory) -> None:
    # Given
    leaky_reply = "Contact support@example.org or +44 20 7946 0958."
    model = ScriptedChatModel(messages=iter([AIMessage(content=leaky_reply)]))

    # When
    response = client_for(model).post("/chat", json={"session_id": "s1", "message": "Who do I call?"})

    # Then
    assert response.status_code == 200
    assert response.json()["reply"] == "Contact [REDACTED_EMAIL] or [REDACTED_PHONE]."
    assert response.json()["guardrails"] == [PII_ACTION]


def test_redaction_is_listed_once_when_both_directions_redact(client_for: ClientFactory) -> None:
    # Given
    model = ScriptedChatModel(messages=iter([AIMessage(content="Noted, 555-123-4567.")]))

    # When
    response = client_for(model).post("/chat", json={"session_id": "s1", "message": "My number is 555-987-6543"})

    # Then
    assert response.json()["reply"] == "Noted, [REDACTED_PHONE]."
    assert response.json()["guardrails"] == [PII_ACTION]
