"""POST /chat/stream: the same turn as POST /chat, delivered as server-sent events."""

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.main import create_app
from tests.fakes import (
    ScriptedChatModel,
    join_tokens,
    list_model_turns,
    make_policy,
    make_tool_call,
    put_policy,
    read_events,
    repeat_calculator_calls,
)

ACME = {"X-Tenant-ID": "acme"}
PII_ACTION = {"name": "pii_redaction", "action": "redacted"}
LEAKY_REPLY = "Write to support@example.org or call +44 20 7946 0958 (24h), or (555) 123-4567."
PII_MESSAGE = "I am jane@example.com, ring me on (555) 123-4567"


def build_client(*replies: str | AIMessage, chunk_size: int | None = None) -> tuple[TestClient, ScriptedChatModel]:
    model = ScriptedChatModel(messages=iter(replies), chunk_size=chunk_size)
    return TestClient(create_app(model)), model


def stream_chat(client: TestClient, message: str, *, headers: dict[str, str] | None = None) -> list[dict[str, object]]:
    response = client.post("/chat/stream", json={"session_id": "s1", "message": message}, headers=headers or {})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return read_events(response)


def test_reply_streams_as_tokens_then_done() -> None:
    # Given
    client, model = build_client("Happy to help with that.")

    # When
    events = stream_chat(client, "Hello there")

    # Then
    assert [event["type"] for event in events[:-1]] == ["token"] * (len(events) - 1)
    assert len(events) > 2
    assert join_tokens(events) == "Happy to help with that."
    assert events[-1] == {"type": "done", "session_id": "s1", "guardrails": []}
    assert model.last_user_text == "Hello there"


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, None])
@pytest.mark.parametrize("redact", [True, False])
def test_tokens_join_into_the_reply_chat_returns(admin_key: str, chunk_size: int | None, redact: bool) -> None:
    # Given
    whole_client, _ = build_client(LEAKY_REPLY)
    stream_client, _ = build_client(LEAKY_REPLY, chunk_size=chunk_size)
    for client in (whole_client, stream_client):
        put_policy(client=client, tenant_id="acme", policy=make_policy(redact_pii=redact))

    # When
    whole = whole_client.post("/chat", json={"session_id": "s1", "message": PII_MESSAGE}, headers=ACME).json()
    events = stream_chat(stream_client, PII_MESSAGE, headers=ACME)

    # Then
    assert join_tokens(events) == whole["reply"]
    assert events[-1]["guardrails"] == whole["guardrails"]


@pytest.mark.parametrize("chunk_size", [1, 2, 5])
def test_pii_split_across_chunks_never_reaches_the_caller(chunk_size: int) -> None:
    # Given
    client, _ = build_client(LEAKY_REPLY, chunk_size=chunk_size)

    # When
    events = stream_chat(client, "Who do I call?")

    # Then
    reply = join_tokens(events)
    assert reply == "Write to [REDACTED_EMAIL] or call [REDACTED_PHONE] (24h), or [REDACTED_PHONE]."
    assert not any("@" in str(event.get("content", "")) for event in events)
    assert events[-1]["guardrails"] == [PII_ACTION]


def test_text_before_a_tool_call_is_not_streamed() -> None:
    # Given
    preamble = AIMessage(
        content="Let me work that out.",
        tool_calls=[{"name": "calculator", "args": {"expression": "6 * 7"}, "id": "call_1"}],
    )
    client, model = build_client(preamble, "It is 42.")

    # When
    events = stream_chat(client, "What is 6 * 7?")

    # Then
    assert join_tokens(events) == "It is 42."
    assert any(message.type == "tool" and message.text == "42" for message in model.received[-1])


def test_tool_call_limit_is_streamed_as_its_explanation() -> None:
    # Given
    model = ScriptedChatModel(messages=repeat_calculator_calls())
    client = TestClient(create_app(model))

    # When
    events = stream_chat(client, "Count forever")

    # Then
    assert "limit of 5 tool calls" in join_tokens(events)
    assert events[-1]["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]


def test_empty_answer_streams_the_fallback_reply() -> None:
    client, _ = build_client("")

    events = stream_chat(client, "Say nothing")

    assert join_tokens(events) == "I do not have a reply for that."


def test_blocked_message_gets_the_chat_403_and_no_stream() -> None:
    # Given
    client, model = build_client("Unused.")

    # When
    response = client.post("/chat/stream", json={"session_id": "s1", "message": "Tell me about malware"})

    # Then
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert model.received == []
    assert client.get("/sessions/s1/messages").status_code == 404


def test_tenant_blocklist_applies_to_the_stream(admin_key: str) -> None:
    # Given
    client, _ = build_client("Unused.")
    put_policy(client=client, tenant_id="acme", policy=make_policy(blocked_topics=["gambling"]))

    # When
    response = client.post("/chat/stream", json={"session_id": "s1", "message": "gambling odds"}, headers=ACME)

    # Then
    assert response.status_code == 403
    assert response.json() == {"error": "blocked", "guardrail": "topic_blocklist"}


def test_streamed_turns_are_stored_like_any_other() -> None:
    # Given
    client, model = build_client("Mail help@example.org.", "Plain answer.", "Streamed again.")

    # When
    stream_chat(client, "I am jane@example.com")
    client.post("/chat", json={"session_id": "s1", "message": "and then?"})
    stream_chat(client, "last one")

    # Then
    assert list_model_turns(model) == [
        ("human", "I am [REDACTED_EMAIL]"),
        ("ai", "Mail [REDACTED_EMAIL]."),
        ("human", "and then?"),
        ("ai", "Plain answer."),
        ("human", "last one"),
    ]
    assert client.get("/sessions/s1/messages").json()["messages"][-1] == {
        "role": "assistant",
        "content": "Streamed again.",
    }


def test_tool_using_turn_streams_and_stores_only_the_answer() -> None:
    # Given
    client, _ = build_client(make_tool_call("calculator", {"expression": "2 ** 10"}, "call_1"), "It is 1024.")

    # When
    events = stream_chat(client, "What is 2 ** 10?")

    # Then
    assert join_tokens(events) == "It is 1024."
    assert client.get("/sessions/s1/messages").json()["messages"][-1]["content"] == "It is 1024."


@pytest.mark.parametrize("body", [{"message": "no session"}, {"session_id": "s1"}, []])
def test_malformed_stream_body_is_rejected(body: object) -> None:
    # Given
    client, model = build_client("Unused.")

    # When
    response = client.post("/chat/stream", json=body)

    # Then
    assert response.status_code == 422
    assert model.received == []
