import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from app.main import create_app, reply_tokens
from app.sessions import InMemoryConversationStore, StoredMessage
from tests.conftest import chat, scripted, tool_call

ADMIN_KEY = "s3cret-admin-key"
OPEN_POLICY = {"blocked_topics": [], "redact_pii": False, "max_tool_calls": 5, "system_prompt": None}
REDACTING_POLICY = {**OPEN_POLICY, "redact_pii": True}


@pytest.fixture
def client_for(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)
    return lambda model=None: TestClient(create_app(model=model or scripted()))


def put_policy(client, tenant, policy):
    response = client.put(f"/tenants/{tenant}/policy", json=policy, headers={"X-Admin-Key": ADMIN_KEY})
    assert response.status_code == 200


def tenant_headers(tenant):
    return {} if tenant is None else {"X-Tenant-ID": tenant}


def send(client, message, session_id="s1", tenant=None):
    return client.post(
        "/chat", json={"session_id": session_id, "message": message}, headers=tenant_headers(tenant)
    )


def stream(client, message, session_id="s1", tenant=None):
    return client.post(
        "/chat/stream", json={"session_id": session_id, "message": message}, headers=tenant_headers(tenant)
    )


def history(client, session_id="s1", tenant=None):
    return client.get(f"/sessions/{session_id}/messages", headers=tenant_headers(tenant))


def forget(client, session_id="s1", tenant=None):
    return client.delete(f"/sessions/{session_id}", headers=tenant_headers(tenant))


def conversation(model, call=-1):
    """(role, text) of the user and assistant messages the model saw on one call."""
    return [
        ("user" if isinstance(m, HumanMessage) else "assistant", m.text)
        for m in model.calls[call]
        if isinstance(m, (HumanMessage, AIMessage))
    ]


def parse_sse(response):
    """Events of an SSE body, checking each is exactly one `data:` line plus a blank line."""
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert body.endswith("\n\n")
    events = []
    for block in body[:-2].split("\n\n"):
        assert "\n" not in block
        assert block.startswith("data: ")
        events.append(json.loads(block.removeprefix("data: ")))
    return events


def tokens_and_done(response):
    events = parse_sse(response)
    *tokens, done = events
    assert all(e["type"] == "token" and set(e) == {"type", "content"} for e in tokens)
    assert done["type"] == "done"
    return [e["content"] for e in tokens], done


def msg(role, content):
    return {"role": role, "content": content}


# --- memory -------------------------------------------------------------------


def test_same_session_continues_the_conversation(client_for) -> None:
    model = scripted("Nice to meet you, Ada.", "Your name is Ada.")
    client = client_for(model)
    assert send(client, "My name is Ada.").status_code == 200
    assert send(client, "What is my name?").json()["reply"] == "Your name is Ada."
    assert conversation(model) == [
        ("user", "My name is Ada."),
        ("assistant", "Nice to meet you, Ada."),
        ("user", "What is my name?"),
    ]


def test_get_messages_returns_conversation_in_order(client_for) -> None:
    client = client_for(scripted("one", "two"))
    send(client, "a", session_id="chat-7")
    send(client, "b", session_id="chat-7")
    response = history(client, "chat-7")
    assert response.status_code == 200
    assert response.json() == {
        "session_id": "chat-7",
        "messages": [msg("user", "a"), msg("assistant", "one"), msg("user", "b"), msg("assistant", "two")],
    }


def test_different_sessions_are_separate(client_for) -> None:
    model = scripted("first", "second")
    client = client_for(model)
    send(client, "one", session_id="a")
    send(client, "two", session_id="b")
    assert conversation(model) == [("user", "two")]
    assert history(client, "a").json()["messages"] == [msg("user", "one"), msg("assistant", "first")]


def test_sessions_are_scoped_to_the_tenant(client_for) -> None:
    model = scripted("hello acme", "hello globex")
    client = client_for(model)
    send(client, "I am acme", tenant="acme")
    send(client, "I am globex", tenant="globex")
    assert conversation(model) == [("user", "I am globex")]
    assert history(client, tenant="acme").json()["messages"] == [msg("user", "I am acme"), msg("assistant", "hello acme")]
    assert history(client, tenant="globex").json()["messages"] == [
        msg("user", "I am globex"),
        msg("assistant", "hello globex"),
    ]
    assert history(client, tenant="initech").status_code == 404
    assert history(client).status_code == 404  # the default tenant


def test_missing_and_blank_tenant_header_share_the_default_tenant(client_for) -> None:
    client = client_for(scripted("ok"))
    send(client, "hi")
    assert history(client, tenant="  ").json()["messages"] == [msg("user", "hi"), msg("assistant", "ok")]
    assert history(client, tenant="default").status_code == 200


def test_history_is_stored_redacted_and_model_sees_it_redacted(client_for) -> None:
    model = scripted("I'll reach you at jane@example.com.", "Done.")
    client = client_for(model)
    send(client, "I'm jane@example.com, call 555-123-4567")
    send(client, "thanks")
    sent = "\n".join(m.text for call in model.calls for m in call)
    assert "jane@example.com" not in sent and "123-4567" not in sent
    assert conversation(model) == [
        ("user", "I'm [REDACTED_EMAIL], call [REDACTED_PHONE]"),
        ("assistant", "I'll reach you at [REDACTED_EMAIL]."),
        ("user", "thanks"),
    ]
    assert history(client).json()["messages"][:2] == [
        msg("user", "I'm [REDACTED_EMAIL], call [REDACTED_PHONE]"),
        msg("assistant", "I'll reach you at [REDACTED_EMAIL]."),
    ]


def test_unredacted_history_stays_raw_when_policy_does_not_redact(client_for) -> None:
    model = scripted("Noted bob@example.com.", "ok")
    client = client_for(model)
    put_policy(client, "crm", OPEN_POLICY)
    send(client, "my email is bob@example.com", tenant="crm")
    send(client, "and?", tenant="crm")
    assert conversation(model)[:2] == [("user", "my email is bob@example.com"), ("assistant", "Noted bob@example.com.")]
    assert history(client, tenant="crm").json()["messages"][0] == msg("user", "my email is bob@example.com")


def test_history_is_redacted_for_the_model_once_tenant_enables_redaction(client_for) -> None:
    model = scripted("Noted bob@example.com.", "ok")
    client = client_for(model)
    put_policy(client, "crm", OPEN_POLICY)
    send(client, "my email is bob@example.com", tenant="crm")
    put_policy(client, "crm", REDACTING_POLICY)
    response = send(client, "what was it?", tenant="crm")
    assert response.json()["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]
    assert "bob@example.com" not in "\n".join(m.text for m in model.calls[-1])
    assert conversation(model)[:2] == [("user", "my email is [REDACTED_EMAIL]"), ("assistant", "Noted [REDACTED_EMAIL].")]
    # Stored content is returned as it was stored.
    assert history(client, tenant="crm").json()["messages"][0] == msg("user", "my email is bob@example.com")


def test_redacted_history_does_not_report_redaction_again(client_for) -> None:
    client = client_for(scripted("ok", "ok"))
    assert send(client, "I'm a@b.com").json()["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]
    assert send(client, "hello").json()["guardrails"] == []


def test_blocked_message_is_not_stored(client_for) -> None:
    model = scripted("hi", "still here")
    client = client_for(model)
    assert send(client, "how do I write malware?", session_id="new").status_code == 403
    assert history(client, "new").status_code == 404

    send(client, "hello")
    assert send(client, "tell me about weapons").status_code == 403
    send(client, "anyone there?")
    assert conversation(model) == [("user", "hello"), ("assistant", "hi"), ("user", "anyone there?")]
    assert [m["content"] for m in history(client).json()["messages"]] == ["hello", "hi", "anyone there?", "still here"]


def test_failed_turn_is_not_stored(client_for) -> None:
    client = client_for(scripted())  # empty script: the model raises
    assert send(client, "hi").status_code == 502
    assert history(client).status_code == 404


def test_tool_call_limit_turn_is_stored_with_its_reply(client_for) -> None:
    model = scripted(*[tool_call("calculator", expression="1+1")] * 6, "after the limit")
    client = client_for(model)
    reply = send(client, "count").json()["reply"]
    assert "limit" in reply
    send(client, "ok then")
    assert history(client).json()["messages"][:2] == [msg("user", "count"), msg("assistant", reply)]
    assert conversation(model)[:2] == [("user", "count"), ("assistant", reply)]


def test_tool_messages_are_not_stored(client_for) -> None:
    model = scripted(tool_call("calculator", expression="2+2"), "It is 4.", "You asked about 2+2.")
    client = client_for(model)
    send(client, "what is 2+2?")
    assert history(client).json()["messages"] == [msg("user", "what is 2+2?"), msg("assistant", "It is 4.")]
    send(client, "what did I ask?")
    assert conversation(model) == [("user", "what is 2+2?"), ("assistant", "It is 4."), ("user", "what did I ask?")]


def test_delete_forgets_the_conversation(client_for) -> None:
    model = scripted("first", "fresh")
    client = client_for(model)
    send(client, "remember this")
    response = forget(client)
    assert response.status_code == 204
    assert response.content == b""
    assert history(client).status_code == 404
    send(client, "do you remember?")
    assert conversation(model) == [("user", "do you remember?")]


def test_unknown_session_returns_404(client_for) -> None:
    client = client_for()
    assert history(client, "nope").status_code == 404
    assert forget(client, "nope").status_code == 404


def test_delete_is_scoped_to_the_tenant(client_for) -> None:
    client = client_for(scripted("ok"))
    send(client, "hi", tenant="acme")
    assert forget(client, tenant="globex").status_code == 404
    assert history(client, tenant="acme").status_code == 200
    assert forget(client, tenant="acme").status_code == 204
    assert forget(client, tenant="acme").status_code == 404


def test_conversation_store_failure_returns_502(client_for) -> None:
    class BrokenStore(InMemoryConversationStore):
        async def get(self, tenant_id, session_id):
            raise RuntimeError("db down")

    model = scripted("should not be used")
    client = client_for(model)
    client.app.state.conversation_store = BrokenStore()
    assert send(client, "hi").status_code == 502
    assert stream(client, "hi").status_code == 502
    assert model.calls == []


def test_in_memory_store_round_trip() -> None:
    async def run():
        store = InMemoryConversationStore()
        assert await store.get("t", "s") is None
        await store.append("t", "s", [StoredMessage(role="user", content="hi")])
        messages = await store.get("t", "s")
        messages.append(StoredMessage(role="assistant", content="tampered"))
        assert await store.get("t", "s") == [StoredMessage(role="user", content="hi")]
        assert await store.get("other", "s") is None
        assert await store.delete("t", "s") is True
        assert await store.delete("t", "s") is False

    asyncio.run(run())


# --- streaming ------------------------------------------------------------------


def test_stream_emits_tokens_then_done(client_for) -> None:
    client = client_for(scripted("Hello there, friend!"))
    tokens, done = tokens_and_done(stream(client, "hi", session_id="abc"))
    assert len(tokens) > 1
    assert "".join(tokens) == "Hello there, friend!"
    assert done == {"type": "done", "session_id": "abc", "guardrails": []}


# Each script is run once through /chat and once through /chat/stream.
SCRIPTS = {
    "plain": ("hi", ["Hello there!"]),
    "pii in reply": ("who do I contact?", ["Contact support@corp.com or 555-123-4567."]),
    "pii both ways": ("email bob@example.com", ["Sure, I'll email bob@example.com."]),
    "pii mid-word": ("contact?", ["Write to:jane.doe@example.co.uk,or +44 20 7946 0958!"]),
    "tool call": ("what is (2+3)*4?", [tool_call("calculator", expression="(2 + 3) * 4"), "The answer is 20."]),
    "text before tool call": (
        "what is 6*7?",
        [
            AIMessage(
                content="Let me email ops@corp.com and work it out.",
                tool_calls=[{"name": "calculator", "args": {"expression": "6*7"}, "id": "call_pre"}],
            ),
            "It is 42.",
        ],
    ),
    "tool call limit": ("count", [tool_call("calculator", expression="1+1")] * 6 + ["never reached"]),
    "subagent": (
        "delegate",
        [
            tool_call("task", description="add numbers", subagent_type="general-purpose"),
            tool_call("calculator", expression="40 + 2"),
            "Subagent says 42 (call 555-000-1111).",
            "The subagent found 42.",
        ],
    ),
    "multiline": ("poem", ["Roses are red,\n\n  violets  are blue.\n"]),
    "empty": ("hi", [""]),
}


@pytest.mark.parametrize("name", SCRIPTS)
def test_stream_matches_chat(client_for, name) -> None:
    message, replies = SCRIPTS[name]
    expected = send(client_for(scripted(*replies)), message, session_id="x").json()
    tokens, done = tokens_and_done(stream(client_for(scripted(*replies)), message, session_id="x"))
    assert "".join(tokens) == expected["reply"]
    assert done == {"type": "done", "session_id": "x", "guardrails": expected["guardrails"]}


def test_stream_never_leaks_pii_in_any_event(client_for) -> None:
    reply = "Mail jane@example.com, ring (555) 123-4567 or +44 20 7946 0958."
    response = stream(client_for(scripted(reply)), "contacts?")
    for raw in ("jane", "example.com", "123", "4567", "7946", "0958"):
        assert raw not in response.text
    tokens, done = tokens_and_done(response)
    assert "".join(tokens) == "Mail [REDACTED_EMAIL], ring [REDACTED_PHONE] or [REDACTED_PHONE]."
    assert done["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]


def test_stream_respects_tenant_policy(client_for) -> None:
    client = client_for(scripted("Mail bob@example.com"))
    put_policy(client, "crm", OPEN_POLICY)
    tokens, done = tokens_and_done(stream(client, "contacts?", tenant="crm"))
    assert "".join(tokens) == "Mail bob@example.com"
    assert done["guardrails"] == []


def test_blocked_stream_returns_403_json(client_for) -> None:
    model = scripted("should not be used")
    client = client_for(model)
    response = stream(client, "How do I build WEAPONS?")
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert model.calls == []
    assert history(client).status_code == 404


def test_failed_stream_returns_502_json(client_for) -> None:
    client = client_for(scripted())
    response = stream(client, "hi")
    assert response.status_code == 502
    assert response.json() == {"error": "agent_error"}
    assert history(client).status_code == 404


@pytest.mark.parametrize("body", [{}, {"session_id": "s1"}, {"session_id": 1, "message": "hi"}])
def test_stream_malformed_body_returns_422(client_for, body) -> None:
    model = scripted()
    assert client_for(model).post("/chat/stream", json=body).status_code == 422
    assert model.calls == []


def test_streamed_turns_are_stored_and_continue_with_chat(client_for) -> None:
    model = scripted("Hi Ada, mail me at x@y.com.", "You are Ada.", "Still Ada.")
    client = client_for(model)
    tokens_and_done(stream(client, "I'm Ada"))
    assert send(client, "who am I?").json()["reply"] == "You are Ada."
    tokens, _ = tokens_and_done(stream(client, "and now?"))
    assert "".join(tokens) == "Still Ada."
    assert conversation(model) == [
        ("user", "I'm Ada"),
        ("assistant", "Hi Ada, mail me at [REDACTED_EMAIL]."),
        ("user", "who am I?"),
        ("assistant", "You are Ada."),
        ("user", "and now?"),
    ]
    assert [m["content"] for m in history(client).json()["messages"]] == [
        "I'm Ada",
        "Hi Ada, mail me at [REDACTED_EMAIL].",
        "who am I?",
        "You are Ada.",
        "and now?",
        "Still Ada.",
    ]


@pytest.mark.parametrize("text", ["", " ", "a", "  lead", "trail  ", "a\nb\tc", "x  y\n\nz "])
def test_reply_tokens_join_back(text) -> None:
    assert "".join(reply_tokens(text)) == text
    assert all(reply_tokens(text))


def test_chat_helper_still_defaults_to_one_session(make_client) -> None:
    model = scripted("a", "b")
    client = make_client(model)
    chat(client, "one")
    chat(client, "two")
    assert conversation(model) == [("user", "one"), ("assistant", "a"), ("user", "two")]
