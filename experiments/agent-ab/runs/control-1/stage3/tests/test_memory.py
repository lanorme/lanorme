import asyncio

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from app.conversations import InMemoryConversationStore, StoredMessage
from app.main import create_app
from tests.conftest import make_model, tool_call

ADMIN_KEY = "k"


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)


def make_client(responses=()):
    model = make_model(responses)
    return TestClient(create_app(model)), model


def chat(client, message, session="s", tenant=None):
    headers = {"X-Tenant-ID": tenant} if tenant is not None else {}
    return client.post("/chat", json={"session_id": session, "message": message}, headers=headers)


def messages(client, session="s", tenant=None):
    headers = {"X-Tenant-ID": tenant} if tenant is not None else {}
    return client.get(f"/sessions/{session}/messages", headers=headers)


def conversation(model, call):
    """The (role, content) of the human/AI messages the model saw in one call."""
    roles = {HumanMessage: "user", AIMessage: "assistant"}
    return [(roles[type(m)], m.content) for m in model.seen[call] if type(m) in roles]


def put_policy(client, tenant, **overrides):
    body = {
        "blocked_topics": ["weapons"],
        "redact_pii": True,
        "max_tool_calls": 5,
        "system_prompt": None,
        **overrides,
    }
    assert client.put(f"/tenants/{tenant}/policy", json=body, headers={"X-Admin-Key": ADMIN_KEY}).status_code == 200


def test_model_sees_earlier_turns_in_order():
    client, model = make_client(["Hi Ann.", "You are Ann.", "Bye."])
    chat(client, "I am Ann")
    chat(client, "Who am I?")
    chat(client, "bye")
    assert conversation(model, 1) == [
        ("user", "I am Ann"),
        ("assistant", "Hi Ann."),
        ("user", "Who am I?"),
    ]
    assert conversation(model, 2)[-2:] == [("assistant", "You are Ann."), ("user", "bye")]


def test_get_messages_returns_conversation_in_order():
    client, _ = make_client(["one", "two"])
    chat(client, "first")
    chat(client, "second")
    resp = messages(client)
    assert resp.status_code == 200
    assert resp.json() == {
        "session_id": "s",
        "messages": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "one"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "two"},
        ],
    }


def test_unknown_session_is_404():
    client, _ = make_client()
    assert messages(client, "nope").status_code == 404
    assert client.delete("/sessions/nope").status_code == 404


def test_sessions_are_scoped_to_tenant():
    client, model = make_client(["for acme", "for globex", "acme again"])
    chat(client, "acme secret", tenant="acme")
    chat(client, "globex hello", tenant="globex")
    chat(client, "again", tenant="acme")
    # Globex's first turn saw nothing of acme's conversation with the same session_id.
    assert conversation(model, 1) == [("user", "globex hello")]
    assert [c for _, c in conversation(model, 2)] == ["acme secret", "for acme", "again"]
    assert [m["content"] for m in messages(client, tenant="globex").json()["messages"]] == [
        "globex hello",
        "for globex",
    ]
    assert messages(client, tenant="default").status_code == 404
    assert messages(client).status_code == 404


def test_missing_or_empty_tenant_header_is_default_tenant():
    client, _ = make_client(["ok"])
    chat(client, "hi")
    assert messages(client, tenant="").status_code == 200
    assert messages(client, tenant="default").status_code == 200
    assert client.delete("/sessions/s", headers={"X-Tenant-ID": "default"}).status_code == 204


def test_blocked_message_is_not_stored():
    client, model = make_client(["fine", "still fine"])
    assert chat(client, "talk about weapons").status_code == 403
    assert messages(client).status_code == 404
    chat(client, "hello")
    assert chat(client, "now malware").status_code == 403
    chat(client, "and?")
    assert [m["content"] for m in messages(client).json()["messages"]] == [
        "hello",
        "fine",
        "and?",
        "still fine",
    ]
    assert all("malware" not in c for _, c in conversation(model, 1))


def test_pii_is_stored_redacted_and_model_sees_redacted_history():
    client, model = make_client(["Saved a@b.com and 555-123-4567.", "ok"])
    chat(client, "my mail is jane@example.com")
    chat(client, "thanks")
    assert messages(client).json()["messages"][:2] == [
        {"role": "user", "content": "my mail is [REDACTED_EMAIL]"},
        {"role": "assistant", "content": "Saved [REDACTED_EMAIL] and [REDACTED_PHONE]."},
    ]
    seen = " ".join(c for _, c in conversation(model, 1))
    for raw in ("jane@example.com", "a@b.com", "4567"):
        assert raw not in seen


def test_without_redaction_content_is_stored_as_sent():
    client, model = make_client(["Reach me at a@b.com", "ok"])
    put_policy(client, "open", redact_pii=False)
    chat(client, "I'm jane@example.com", tenant="open")
    chat(client, "next", tenant="open")
    assert [m["content"] for m in messages(client, tenant="open").json()["messages"][:2]] == [
        "I'm jane@example.com",
        "Reach me at a@b.com",
    ]
    assert conversation(model, 1)[:2] == [
        ("user", "I'm jane@example.com"),
        ("assistant", "Reach me at a@b.com"),
    ]


def test_history_stored_unredacted_is_redacted_once_tenant_redacts():
    client, model = make_client(["noted", "ok"])
    put_policy(client, "t", redact_pii=False)
    chat(client, "I'm jane@example.com", tenant="t")
    put_policy(client, "t", redact_pii=True)
    chat(client, "hi", tenant="t")
    assert conversation(model, 1)[0] == ("user", "I'm [REDACTED_EMAIL]")
    # Stored content is returned as stored.
    assert messages(client, tenant="t").json()["messages"][0]["content"] == "I'm jane@example.com"


def test_delete_forgets_conversation():
    client, model = make_client(["one", "fresh"])
    chat(client, "remember me")
    resp = client.delete("/sessions/s")
    assert resp.status_code == 204
    assert resp.content == b""
    assert messages(client).status_code == 404
    assert client.delete("/sessions/s").status_code == 404
    chat(client, "who?")
    assert conversation(model, 1) == [("user", "who?")]


def test_delete_is_scoped_to_tenant():
    client, _ = make_client(["a", "b"])
    chat(client, "x", tenant="acme")
    chat(client, "y", tenant="globex")
    assert client.delete("/sessions/s", headers={"X-Tenant-ID": "initech"}).status_code == 404
    assert client.delete("/sessions/s", headers={"X-Tenant-ID": "acme"}).status_code == 204
    assert messages(client, tenant="acme").status_code == 404
    assert messages(client, tenant="globex").status_code == 200


def test_tool_turns_store_only_user_and_final_reply():
    client, model = make_client(
        [
            AIMessage(content="", tool_calls=[tool_call("calculator", {"expression": "6*7"}, "c1")]),
            "42.",
            "yes",
        ]
    )
    chat(client, "6*7?")
    chat(client, "sure?")
    assert messages(client).json()["messages"][:2] == [
        {"role": "user", "content": "6*7?"},
        {"role": "assistant", "content": "42."},
    ]
    assert conversation(model, 2) == [("user", "6*7?"), ("assistant", "42."), ("user", "sure?")]


def test_tool_limit_turn_is_stored_with_its_reply():
    calls = [
        AIMessage(content="", tool_calls=[tool_call("calculator", {"expression": "1+1"}, f"c{i}")])
        for i in range(6)
    ]
    client, _ = make_client(calls)
    reply = chat(client, "loop").json()["reply"]
    assert messages(client).json()["messages"] == [
        {"role": "user", "content": "loop"},
        {"role": "assistant", "content": reply},
    ]


def test_tool_limit_is_per_turn_with_history():
    # History is text only, so earlier turns' tool calls don't count against this turn.
    calls = [
        AIMessage(content="", tool_calls=[tool_call("calculator", {"expression": "1+1"}, f"c{i}")])
        for i in range(8)
    ]
    client, _ = make_client([*calls[:4], "first", *calls[4:], "second"])
    chat(client, "a")
    resp = chat(client, "b")
    assert resp.json() == {"session_id": "s", "reply": "second", "guardrails": []}


def test_conversation_store_is_replaceable():
    class RecordingStore(InMemoryConversationStore):
        def __init__(self):
            super().__init__()
            self.appended = []

        async def append(self, tenant_id, session_id, messages):
            self.appended.append((tenant_id, session_id, list(messages)))
            await super().append(tenant_id, session_id, messages)

    client, _ = make_client(["ok"])
    client.app.state.conversations = store = RecordingStore()
    chat(client, "hi", tenant="acme")
    assert store.appended == [
        ("acme", "s", [StoredMessage(role="user", content="hi"), StoredMessage(role="assistant", content="ok")])
    ]
    assert messages(client, tenant="acme").status_code == 200


def test_in_memory_conversation_store():
    store = InMemoryConversationStore()
    hi = StoredMessage(role="user", content="hi")
    yo = StoredMessage(role="assistant", content="yo")

    async def scenario():
        assert await store.get("t", "s") is None
        await store.append("t", "s", [hi, yo])
        got = await store.get("t", "s")
        assert got == [hi, yo]
        got.append(hi)  # callers get a copy
        assert await store.get("t", "s") == [hi, yo]
        assert await store.get("other", "s") is None
        assert await store.delete("other", "s") is False
        assert await store.delete("t", "s") is True
        assert await store.delete("t", "s") is False
        assert await store.get("t", "s") is None

    asyncio.run(scenario())
