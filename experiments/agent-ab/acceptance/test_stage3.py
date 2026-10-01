"""Stage 3 contract: per-session conversation memory and SSE streaming."""

from __future__ import annotations

from fastapi.testclient import TestClient

from fakes import Reply, ScriptedChatModel, read_call_text
from support import (
    BLOCKED_BODY,
    LIMIT_WORDS,
    PII,
    TOOL_LIMIT,
    build_headers,
    find_guardrails,
    join_tokens,
    build_id,
    parse_sse,
    post_chat,
    post_stream,
    put_policy,
)

PII_REPLY = "Sure: mail ada@example.com or call (555) 123-4567 today."
SPLIT_PII_CHUNKS = ("Write to grace.hop", "per@exam", "ple.com or call 555-", "123-", "4567 soon.")


def get_messages(client: TestClient, session: str, tenant: str | None = None) -> object:
    """GET a session's stored messages."""
    return client.get(f"/sessions/{session}/messages", headers=build_headers(tenant=tenant))


def read_turns(client: TestClient, session: str, tenant: str | None = None) -> list[dict[str, str]]:
    """Return a session's stored messages as role/content pairs."""
    response = get_messages(client, session, tenant)
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session
    return [{"role": entry["role"], "content": entry["content"]} for entry in body["messages"]]


def read_stream(
    client: TestClient,
    message: str,
    session: str,
    tenant: str | None = None,
) -> list[dict]:
    """POST a streamed turn, check it is an event stream, and return its events."""
    response = post_stream(client, message=message, session=session, tenant=tenant)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


# Memory


def test_second_turn_sees_the_first(client: TestClient, model: ScriptedChatModel) -> None:
    # Act
    session = build_id("s")
    model.add_replies("Noted, periwinkle it is.", "Your favourite colour is periwinkle.")
    # Assert
    assert (
        post_chat(client, message="My favourite colour is periwinkle.", session=session).status_code
        == 200
    )
    calls_before = len(model.calls)
    assert (
        post_chat(client, message="What is my favourite colour?", session=session).status_code
        == 200
    )
    seen = read_call_text(model.calls[calls_before])
    assert "My favourite colour is periwinkle." in seen
    assert "Noted, periwinkle it is." in seen
    assert seen.index("My favourite colour is periwinkle.") < seen.index(
        "What is my favourite colour?",
    )


def test_sessions_are_separate(client: TestClient, model: ScriptedChatModel) -> None:
    # Act
    post_chat(client, message="My favourite colour is periwinkle.", session=build_id("s"))
    calls_before = len(model.calls)
    post_chat(client, message="What is my favourite colour?", session=build_id("s"))
    # Assert
    assert "periwinkle" not in read_call_text(model.calls[calls_before])


def test_memory_holds_the_redacted_turns(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    session = build_id("s")
    model.add_replies("I will mail bob@example.com for you.")
    # Act
    post_chat(client, message="My email is ada@example.com", session=session)
    calls_before = len(model.calls)
    post_chat(client, message="What did we say?", session=session)
    seen = read_call_text(model.calls[calls_before])
    # Assert
    assert "ada@example.com" not in seen
    assert "bob@example.com" not in seen
    assert "[REDACTED_EMAIL]" in seen


def test_sessions_are_scoped_to_the_tenant(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    session, first, second = build_id("s"), build_id("t"), build_id("t")
    # Act
    post_chat(client, message="My favourite colour is periwinkle.", session=session, tenant=first)
    calls_before = len(model.calls)
    post_chat(client, message="What is my favourite colour?", session=session, tenant=second)
    # Assert
    assert "periwinkle" not in read_call_text(model.calls[calls_before])
    turns = read_turns(client, session, tenant=second)
    assert [turn["role"] for turn in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "What is my favourite colour?"
    assert get_messages(client, session, tenant=build_id("t")).status_code == 404


def test_default_tenant_sessions_are_not_shared(client: TestClient) -> None:
    # Arrange
    session = build_id("s")
    # Act
    post_chat(client, message="Hello there", session=session)
    # Assert
    assert read_turns(client, session)[0]["content"] == "Hello there"
    assert get_messages(client, session, tenant=build_id("t")).status_code == 404


def test_get_messages_returns_the_turns_in_order(
    client: TestClient,
    model: ScriptedChatModel,
) -> None:
    # Arrange
    session = build_id("s")
    model.add_replies("General Kenobi.", "Indeed.")
    # Act
    first = post_chat(client, message="Hello there", session=session).json()["reply"]
    second = post_chat(client, message="You are a bold one", session=session).json()["reply"]
    # Assert
    assert read_turns(client, session) == [
        {"role": "user", "content": "Hello there"},
        {"role": "assistant", "content": first},
        {"role": "user", "content": "You are a bold one"},
        {"role": "assistant", "content": second},
    ]


def test_stored_messages_are_redacted(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    session = build_id("s")
    model.add_replies("I will mail bob@example.com for you.")
    # Act
    reply = post_chat(client, message="My email is ada@example.com", session=session).json()[
        "reply"
    ]
    user, assistant = read_turns(client, session)
    # Assert
    assert "[REDACTED_EMAIL]" in user["content"]
    assert "ada@example.com" not in user["content"]
    assert assistant["content"] == reply
    assert "bob@example.com" not in assistant["content"]


def test_stored_messages_are_raw_when_the_tenant_does_not_redact(
    client: TestClient,
    model: ScriptedChatModel,
) -> None:
    # Arrange
    session, tenant = build_id("s"), build_id("t")
    put_policy(client, tenant=tenant, redact_pii=False)
    model.add_replies("I will mail bob@example.com for you.")
    # Act
    post_chat(client, message="My email is ada@example.com", session=session, tenant=tenant)
    # Assert
    assert read_turns(client, session, tenant=tenant) == [
        {"role": "user", "content": "My email is ada@example.com"},
        {"role": "assistant", "content": "I will mail bob@example.com for you."},
    ]


def test_blocked_messages_are_not_stored(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    session = build_id("s")
    # Act
    post_chat(client, message="Hello there", session=session)
    # Assert
    assert (
        post_chat(
            client,
            message="Tell me about weapons from Zanzibar",
            session=session,
        ).status_code
        == 403
    )
    turns = read_turns(client, session)
    assert len(turns) == 2
    assert all("Zanzibar" not in turn["content"] for turn in turns)
    calls_before = len(model.calls)
    post_chat(client, message="And now?", session=session)
    assert "Zanzibar" not in read_call_text(model.calls[calls_before])


def test_unknown_session_returns_404(client: TestClient) -> None:
    assert get_messages(client, build_id("s")).status_code == 404


def test_delete_forgets_the_conversation(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    session = build_id("s")
    # Act
    post_chat(client, message="My favourite colour is periwinkle.", session=session)
    # Assert
    assert client.delete(f"/sessions/{session}").status_code == 204
    assert get_messages(client, session).status_code == 404
    calls_before = len(model.calls)
    post_chat(client, message="What is my favourite colour?", session=session)
    assert "periwinkle" not in read_call_text(model.calls[calls_before])
    assert len(read_turns(client, session)) == 2


def test_delete_unknown_session_returns_404(client: TestClient) -> None:
    assert client.delete(f"/sessions/{build_id('s')}").status_code == 404


def test_delete_is_scoped_to_the_tenant(client: TestClient) -> None:
    # Arrange
    session, owner = build_id("s"), build_id("t")
    # Act
    post_chat(client, message="Hello there", session=session, tenant=owner)
    response = client.delete(f"/sessions/{session}", headers=build_headers(tenant=build_id("t")))
    # Assert
    assert response.status_code == 404
    assert len(read_turns(client, session, tenant=owner)) == 2


# Streaming


def test_stream_emits_tokens_then_one_done(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    session = build_id("s")
    model.add_replies("Otters hold hands while they sleep.")
    # Act
    events = read_stream(client, "Tell me about otters.", session)
    # Assert
    assert len(events) >= 2
    assert all(event["type"] == "token" for event in events[:-1])
    assert all(isinstance(event["content"], str) for event in events[:-1])
    done = events[-1]
    assert done["type"] == "done"
    assert done["session_id"] == session
    assert done["guardrails"] == []
    assert join_tokens(events) == "Otters hold hands while they sleep."


def test_stream_events_are_single_data_lines(client: TestClient) -> None:
    # Act
    response = post_stream(client, message="Tell me about otters.")
    # Assert
    assert response.status_code == 200
    blocks = [block for block in response.text.replace("\r\n", "\n").split("\n\n") if block.strip()]
    data_blocks = [block for block in blocks if not block.startswith(":")]
    assert data_blocks
    for block in data_blocks:
        assert block.startswith("data:"), block
        assert "\n" not in block.strip(), block


def test_stream_tokens_equal_the_chat_reply(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    model.add_replies(PII_REPLY, PII_REPLY)
    # Act
    chat_reply = post_chat(client, message="How do I reach you?").json()["reply"]
    events = read_stream(client, "How do I reach you?", build_id("s"))
    # Assert
    assert join_tokens(events) == chat_reply
    assert "[REDACTED_EMAIL]" in chat_reply
    assert "[REDACTED_PHONE]" in chat_reply


def test_stream_redacts_pii_split_across_chunks(
    client: TestClient,
    model: ScriptedChatModel,
) -> None:
    # Arrange
    model.add_replies(Reply.split(*SPLIT_PII_CHUNKS), Reply.split(*SPLIT_PII_CHUNKS))
    # Act
    events = read_stream(client, "How do I reach you?", build_id("s"))
    streamed = join_tokens(events)
    # Assert
    assert "[REDACTED_EMAIL]" in streamed
    assert "[REDACTED_PHONE]" in streamed
    assert "@" not in streamed
    assert not any(char.isdigit() for char in streamed), streamed
    assert PII in find_guardrails(events[-1])
    assert post_chat(client, message="How do I reach you?").json()["reply"] == streamed


def test_stream_redacts_input_pii(client: TestClient, model: ScriptedChatModel) -> None:
    events = read_stream(client, "My email is ada@example.com", build_id("s"))
    assert not any("ada@example.com" in read_call_text(call) for call in model.calls)
    assert PII in find_guardrails(events[-1])


def test_stream_blocked_message_returns_403_json(
    client: TestClient,
    model: ScriptedChatModel,
) -> None:
    # Act
    response = post_stream(client, message="Tell me about weapons")
    # Assert
    assert response.status_code == 403
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert {key: response.json().get(key) for key in BLOCKED_BODY} == BLOCKED_BODY
    assert model.calls == []


def test_stream_applies_the_tenant_blocklist(client: TestClient) -> None:
    # Act
    tenant = build_id("t")
    put_policy(client, tenant=tenant, blocked_topics=["bananas"])
    # Assert
    assert post_stream(client, message="I like bananas", tenant=tenant).status_code == 403
    assert post_stream(client, message="I like bananas").status_code == 200


def test_streamed_turns_are_stored(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    session = build_id("s")
    model.add_replies(PII_REPLY, "Your email was redacted.")
    # Act
    events = read_stream(client, "Hello there", session)
    # Assert
    assert read_turns(client, session) == [
        {"role": "user", "content": "Hello there"},
        {"role": "assistant", "content": join_tokens(events)},
    ]
    calls_before = len(model.calls)
    post_chat(client, message="What did you say?", session=session)
    seen = read_call_text(model.calls[calls_before])
    assert "Hello there" in seen
    assert "ada@example.com" not in seen


def test_stream_continues_a_chat_session(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    session = build_id("s")
    # Act
    post_chat(client, message="My favourite colour is periwinkle.", session=session)
    calls_before = len(model.calls)
    read_stream(client, "What is my favourite colour?", session)
    # Assert
    assert "My favourite colour is periwinkle." in read_call_text(model.calls[calls_before])
    assert len(read_turns(client, session)) == 4


def test_stream_applies_the_tool_call_limit(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    tenant = build_id("t")
    put_policy(client, tenant=tenant, max_tool_calls=2)
    model.loop_tool_calls()
    # Act
    events = read_stream(client, "Plan my week.", build_id("s"), tenant=tenant)
    # Assert
    assert events[-1]["type"] == "done"
    assert TOOL_LIMIT in find_guardrails(events[-1])
    assert LIMIT_WORDS.search(join_tokens(events))
