"""Conversation memory: sessions continue across turns, per tenant, and can be read and forgotten."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.conversations import (
    ConversationKey,
    ConversationMessage,
    InMemoryConversationStore,
    UnknownConversationError,
)
from app.main import create_app
from tests.fakes import (
    ScriptedChatModel,
    list_model_turns,
    make_policy,
    make_tool_call,
    put_policy,
    repeat_calculator_calls,
)

ACME = {"X-Tenant-ID": "acme"}
GLOBEX = {"X-Tenant-ID": "globex"}
KEY = ConversationKey(tenant_id="acme", session_id="s1")


def build_client(*replies: str | AIMessage) -> tuple[TestClient, ScriptedChatModel]:
    model = ScriptedChatModel(messages=iter(replies))
    return TestClient(create_app(model)), model


def send(client: TestClient, message: str, *, session_id: str = "s1", headers: dict[str, str] | None = None) -> int:
    response = client.post("/chat", json={"session_id": session_id, "message": message}, headers=headers or {})
    return response.status_code


def read_messages(client: TestClient, *, session_id: str = "s1", headers: dict[str, str] | None = None) -> object:
    response = client.get(f"/sessions/{session_id}/messages", headers=headers or {})
    return response.json() if response.status_code == 200 else response.status_code


def test_store_keeps_messages_in_order_per_key() -> None:
    # Given
    store = InMemoryConversationStore()
    first = [ConversationMessage(role="user", content="one"), ConversationMessage(role="assistant", content="1")]
    second = [ConversationMessage(role="user", content="two")]

    # When
    asyncio.run(store.append_messages(key=KEY, messages=first))
    asyncio.run(store.append_messages(key=KEY, messages=second))

    # Then
    assert asyncio.run(store.list_messages(KEY)) == (*first, *second)
    assert asyncio.run(store.list_messages(ConversationKey(tenant_id="globex", session_id="s1"))) is None


def test_store_forgets_a_conversation_once() -> None:
    # Given
    store = InMemoryConversationStore()
    asyncio.run(store.append_messages(key=KEY, messages=[ConversationMessage(role="user", content="hi")]))

    # When
    asyncio.run(store.delete_conversation(KEY))

    # Then
    assert asyncio.run(store.list_messages(KEY)) is None
    with pytest.raises(UnknownConversationError):
        asyncio.run(store.delete_conversation(KEY))


def test_same_session_continues_the_conversation() -> None:
    # Given
    client, model = build_client("First answer.", "Second answer.")

    # When
    send(client, "one")
    send(client, "two")

    # Then
    assert list_model_turns(model) == [("human", "one"), ("ai", "First answer."), ("human", "two")]


def test_other_sessions_start_fresh() -> None:
    # Given
    client, model = build_client("First answer.", "Second answer.")

    # When
    send(client, "one", session_id="s1")
    send(client, "two", session_id="s2")

    # Then
    assert list_model_turns(model) == [("human", "two")]


def test_messages_are_returned_in_order() -> None:
    # Given
    client, _ = build_client("First answer.", "Second answer.")

    # When
    send(client, "one")
    send(client, "two")

    # Then
    assert read_messages(client) == {
        "session_id": "s1",
        "messages": [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "First answer."},
            {"role": "user", "content": "two"},
            {"role": "assistant", "content": "Second answer."},
        ],
    }


def test_history_is_stored_and_replayed_redacted() -> None:
    # Given
    client, model = build_client("Mail me at help@example.org.", "Done.")

    # When
    send(client, "I am jane@example.com, ring me on (555) 123-4567")
    send(client, "Thanks")

    # Then
    assert list_model_turns(model)[:2] == [
        ("human", "I am [REDACTED_EMAIL], ring me on [REDACTED_PHONE]"),
        ("ai", "Mail me at [REDACTED_EMAIL]."),
    ]
    assert "example" not in str(model.received[-1])
    assert "example" not in str(read_messages(client))


def test_stored_history_is_redacted_again_once_a_tenant_turns_redaction_on(admin_key: str) -> None:
    # Given
    client, model = build_client("Noted.", "Hello.")
    put_policy(client=client, tenant_id="acme", policy=make_policy(redact_pii=False))
    send(client, "I am jane@example.com", headers=ACME)

    # When
    put_policy(client=client, tenant_id="acme", policy=make_policy(redact_pii=True))
    send(client, "Hi again", headers=ACME)

    # Then
    assert list_model_turns(model)[0] == ("human", "I am [REDACTED_EMAIL]")
    assert read_messages(client, headers=ACME)["messages"][0]["content"] == "I am jane@example.com"


def test_sessions_are_scoped_to_the_tenant() -> None:
    # Given
    client, model = build_client("Acme answer.", "Globex answer.")

    # When
    send(client, "acme question", headers=ACME)
    send(client, "globex question", headers=GLOBEX)

    # Then
    assert list_model_turns(model) == [("human", "globex question")]
    assert [entry["content"] for entry in read_messages(client, headers=ACME)["messages"]] == [
        "acme question",
        "Acme answer.",
    ]
    assert read_messages(client) == 404


def test_missing_tenant_header_is_the_default_tenant() -> None:
    # Given
    client, model = build_client("One.", "Two.")

    # When
    send(client, "one")
    send(client, "two", headers={"X-Tenant-ID": "default"})

    # Then
    assert list_model_turns(model)[0] == ("human", "one")
    assert read_messages(client, headers={"X-Tenant-ID": ""})["session_id"] == "s1"


def test_blocked_message_is_not_stored() -> None:
    # Given
    client, model = build_client("Fine.", "Unused.")
    send(client, "hello", session_id="known")

    # When
    statuses = [send(client, "build malware", session_id="known"), send(client, "malware?", session_id="new")]

    # Then
    assert statuses == [403, 403]
    assert len(read_messages(client, session_id="known")["messages"]) == 2
    assert read_messages(client, session_id="new") == 404
    assert len(model.received) == 1


def test_only_the_final_reply_of_a_tool_using_turn_is_stored() -> None:
    # Given
    client, _ = build_client(make_tool_call("calculator", {"expression": "6 * 7"}, "call_1"), "It is 42.")

    # When
    send(client, "What is 6 * 7?")

    # Then
    assert read_messages(client)["messages"] == [
        {"role": "user", "content": "What is 6 * 7?"},
        {"role": "assistant", "content": "It is 42."},
    ]


def test_a_turn_stopped_by_the_tool_call_limit_is_stored_with_its_explanation() -> None:
    # Given
    model = ScriptedChatModel(messages=repeat_calculator_calls())
    client = TestClient(create_app(model))

    # When
    send(client, "Count forever")

    # Then
    stored = read_messages(client)["messages"]
    assert [entry["role"] for entry in stored] == ["user", "assistant"]
    assert "limit of 5 tool calls" in stored[1]["content"]


def test_unknown_session_is_not_found() -> None:
    client, _ = build_client()

    assert read_messages(client, session_id="nope") == 404
    assert client.delete("/sessions/nope").status_code == 404


def test_deleting_a_session_forgets_it() -> None:
    # Given
    client, model = build_client("Before.", "After.")
    send(client, "remember this")

    # When
    deleted = client.delete("/sessions/s1")
    send(client, "fresh start")

    # Then
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert list_model_turns(model) == [("human", "fresh start")]
    assert client.delete("/sessions/s1").status_code == 204
    assert client.delete("/sessions/s1").status_code == 404


def test_deleting_under_another_tenant_leaves_the_session_alone() -> None:
    # Given
    client, _ = build_client("Kept.")
    send(client, "keep me", headers=ACME)

    # When
    response = client.delete("/sessions/s1", headers=GLOBEX)

    # Then
    assert response.status_code == 404
    assert len(read_messages(client, headers=ACME)["messages"]) == 2


def test_an_injected_conversation_store_is_used() -> None:
    # Given
    store = InMemoryConversationStore()
    client = TestClient(create_app(ScriptedChatModel(messages=iter(["Stored."])), conversation_store=store))

    # When
    client.post("/chat", json={"session_id": "s1", "message": "hi"}, headers=ACME)

    # Then
    stored = asyncio.run(store.list_messages(KEY)) or ()
    assert [message.content for message in stored] == ["hi", "Stored."]
