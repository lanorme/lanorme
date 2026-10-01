"""The HTTP contract: /health, the /chat happy path and request validation."""

import pytest

from tests.fakes import ClientFactory, ScriptedChatModel


def test_health_reports_ok(client_for: ClientFactory, replying_model: ScriptedChatModel) -> None:
    response = client_for(replying_model).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_returns_the_agent_reply(client_for: ClientFactory, replying_model: ScriptedChatModel) -> None:
    # Given
    client = client_for(replying_model)

    # When
    response = client.post("/chat", json={"session_id": "abc-123", "message": "Hello there"})

    # Then
    assert response.status_code == 200
    assert response.json() == {"session_id": "abc-123", "reply": "Happy to help.", "guardrails": []}
    assert replying_model.last_user_text == "Hello there"


@pytest.mark.parametrize(
    "body",
    [
        {"message": "no session"},
        {"session_id": "s1"},
        {"session_id": 1, "message": "hi"},
        {"session_id": "s1", "message": ["not", "text"]},
        [],
    ],
)
def test_malformed_body_is_rejected(
    client_for: ClientFactory, replying_model: ScriptedChatModel, body: object
) -> None:
    response = client_for(replying_model).post("/chat", json=body)

    assert response.status_code == 422
    assert replying_model.received == []


def test_non_json_body_is_rejected(client_for: ClientFactory, replying_model: ScriptedChatModel) -> None:
    response = client_for(replying_model).post(
        "/chat", content=b"not json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 422


def test_each_request_starts_a_fresh_conversation(client_for: ClientFactory) -> None:
    # Given
    model = ScriptedChatModel(messages=iter(["First answer.", "Second answer."]))
    client = client_for(model)

    # When
    client.post("/chat", json={"session_id": "s1", "message": "one"})
    second = client.post("/chat", json={"session_id": "s1", "message": "two"})

    # Then
    assert second.json()["reply"] == "Second answer."
    human_turns = [message.text for message in model.received[-1] if message.type == "human"]
    assert human_turns == ["two"]
