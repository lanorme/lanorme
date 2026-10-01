"""Conversation memory: what the model sees across turns and the /sessions API."""

import asyncio

import pytest

from app.guardrails import LIMIT_REACHED_REPLY
from app.sessions import ChatMessage, InMemorySessionStore
from tests.conftest import ADMIN_KEY
from tests.fakes import ai, endless, scripted, tool_call

ADMIN = {"X-Admin-Key": ADMIN_KEY}
POLICY = {"blocked_topics": ["weapons"], "redact_pii": True, "max_tool_calls": 5,
          "system_prompt": None}


def chat(client, message, session_id="s1", tenant=None):
    headers = {} if tenant is None else {"X-Tenant-ID": tenant}
    return client.post(
        "/chat", json={"session_id": session_id, "message": message}, headers=headers
    )


def history(client, session_id="s1", tenant=None):
    headers = {} if tenant is None else {"X-Tenant-ID": tenant}
    return client.get(f"/sessions/{session_id}/messages", headers=headers)


def forget(client, session_id="s1", tenant=None):
    headers = {} if tenant is None else {"X-Tenant-ID": tenant}
    return client.delete(f"/sessions/{session_id}", headers=headers)


def set_policy(client, tenant, **overrides):
    r = client.put(f"/tenants/{tenant}/policy", json={**POLICY, **overrides}, headers=ADMIN)
    assert r.status_code == 200


def conversation(model, call=-1):
    """(type, text) of the non-system messages the model got on a given call."""
    return [(m.type, m.text) for m in model.received[call] if m.type != "system"]


def user(content):
    return {"role": "user", "content": content}


def assistant(content):
    return {"role": "assistant", "content": content}


# ------------------------------------------------------------------ memory


def test_model_sees_earlier_turns(make_client):
    model = scripted(ai("first"), ai("second"), ai("third"))
    client = make_client(model)
    chat(client, "one")
    chat(client, "two")
    assert chat(client, "three").json()["reply"] == "third"
    assert conversation(model) == [
        ("human", "one"), ("ai", "first"),
        ("human", "two"), ("ai", "second"),
        ("human", "three"),
    ]


def test_model_sees_earlier_turns_redacted(make_client):
    model = scripted(ai("Noted, I'll write to jane@example.com"), ai("ok"))
    client = make_client(model)
    chat(client, "I'm jane@example.com, call 555-123-4567")
    chat(client, "what's my email?")
    assert conversation(model) == [
        ("human", "I'm [REDACTED_EMAIL], call [REDACTED_PHONE]"),
        ("ai", "Noted, I'll write to [REDACTED_EMAIL]"),
        ("human", "what's my email?"),
    ]
    assert "jane@example.com" not in str(model.received)


def test_only_user_and_assistant_text_is_remembered(make_client):
    model = scripted(
        ai("", tool_call("calculator", expression="6*7")), ai("42"), ai("you asked 6*7")
    )
    client = make_client(model)
    chat(client, "6*7?")
    chat(client, "what did I ask?")
    assert conversation(model) == [("human", "6*7?"), ("ai", "42"), ("human", "what did I ask?")]


def test_sessions_do_not_share_history(make_client):
    model = scripted(ai("a1"), ai("b1"), ai("a2"))
    client = make_client(model)
    chat(client, "hello a", session_id="a")
    chat(client, "hello b", session_id="b")
    chat(client, "again a", session_id="a")
    assert conversation(model, 1) == [("human", "hello b")]
    assert conversation(model, 2) == [("human", "hello a"), ("ai", "a1"), ("human", "again a")]


def test_same_session_id_under_two_tenants_is_two_conversations(make_client):
    model = scripted(ai("for acme"), ai("for globex"), ai("acme again"))
    client = make_client(model)
    chat(client, "acme secret", tenant="acme")
    chat(client, "globex hi", tenant="globex")
    assert conversation(model, 1) == [("human", "globex hi")]
    chat(client, "acme more", tenant="acme")
    assert conversation(model, 2) == [
        ("human", "acme secret"), ("ai", "for acme"), ("human", "acme more")
    ]
    assert history(client, tenant="globex").json()["messages"] == [
        user("globex hi"), assistant("for globex")
    ]


def test_missing_and_blank_tenant_header_share_the_default_tenant(make_client):
    client = make_client(scripted(ai("ok")))
    chat(client, "hi")
    assert history(client).status_code == 200
    assert history(client, tenant="").status_code == 200
    assert history(client, tenant="default").status_code == 200
    assert history(client, tenant="other").status_code == 404


def test_blocked_message_is_not_stored(make_client):
    model = scripted(ai("first"), ai("second"))
    client = make_client(model)
    assert chat(client, "weapons please").status_code == 403
    assert history(client).status_code == 404  # never created

    chat(client, "hi")
    assert chat(client, "now malware").status_code == 403
    chat(client, "bye")
    assert history(client).json()["messages"] == [
        user("hi"), assistant("first"), user("bye"), assistant("second")
    ]
    assert "malware" not in str(model.received)


def test_failed_turn_is_not_stored(make_client):
    responses = iter([ai("first")])

    def next_or_fail():
        reply = next(responses, None)
        if reply is None:
            raise RuntimeError("model down")
        return reply

    client = make_client(endless(next_or_fail))
    chat(client, "hi")
    assert chat(client, "again").status_code == 502
    assert history(client).json()["messages"] == [user("hi"), assistant("first")]


def test_limit_reply_is_stored(make_client):
    model = endless(lambda: ai("", tool_call("calculator", expression="1+1")))
    client = make_client(model, tool_call_limit=1)
    chat(client, "loop")
    assert history(client).json()["messages"] == [
        user("loop"), assistant(LIMIT_REACHED_REPLY.format(limit=1))
    ]


def test_tenant_system_prompt_is_not_stored(make_client):
    client = make_client(scripted(ai("bonjour")))
    set_policy(client, "fr", system_prompt="Answer in French.")
    chat(client, "hi", tenant="fr")
    assert history(client, tenant="fr").json()["messages"] == [user("hi"), assistant("bonjour")]


# ------------------------------------------------------------------ GET /sessions/{id}/messages


def test_get_messages_returns_turns_in_order_as_stored(make_client):
    client = make_client(scripted(ai("Mail support@corp.com"), ai("Sure.")))
    chat(client, "I'm bob@x.org", session_id="conv-1")
    chat(client, "thanks", session_id="conv-1")
    r = history(client, "conv-1")
    assert r.status_code == 200
    assert r.json() == {
        "session_id": "conv-1",
        "messages": [
            user("I'm [REDACTED_EMAIL]"),
            assistant("Mail [REDACTED_EMAIL]"),
            user("thanks"),
            assistant("Sure."),
        ],
    }


def test_get_messages_unredacted_when_policy_does_not_redact(make_client):
    client = make_client(scripted(ai("Mail support@corp.com")))
    set_policy(client, "raw", redact_pii=False)
    chat(client, "I'm bob@x.org", tenant="raw")
    assert history(client, tenant="raw").json()["messages"] == [
        user("I'm bob@x.org"), assistant("Mail support@corp.com")
    ]


def test_history_is_redacted_for_the_model_once_policy_turns_redaction_on(make_client):
    model = scripted(ai("I'll mail support@corp.com"), ai("ok"))
    client = make_client(model)
    set_policy(client, "t", redact_pii=False)
    chat(client, "I'm bob@x.org", tenant="t")
    set_policy(client, "t", redact_pii=True)
    chat(client, "and?", tenant="t")
    assert conversation(model) == [
        ("human", "I'm [REDACTED_EMAIL]"),
        ("ai", "I'll mail [REDACTED_EMAIL]"),
        ("human", "and?"),
    ]
    # Stored content is left as it was stored.
    assert history(client, tenant="t").json()["messages"][0] == user("I'm bob@x.org")


def test_get_unknown_session_is_404(make_client):
    client = make_client(scripted(ai("ok")))
    assert history(client, "nope").status_code == 404
    chat(client, "hi", tenant="acme")
    assert history(client, tenant="other").status_code == 404


# ------------------------------------------------------------------ DELETE /sessions/{id}


def test_delete_forgets_the_conversation(make_client):
    model = scripted(ai("first"), ai("fresh"))
    client = make_client(model)
    chat(client, "remember me")
    r = forget(client)
    assert r.status_code == 204
    assert r.content == b""
    assert history(client).status_code == 404
    chat(client, "who am I?")
    assert conversation(model) == [("human", "who am I?")]


def test_delete_unknown_session_is_404(make_client):
    client = make_client(scripted(ai("ok")))
    assert forget(client, "nope").status_code == 404
    chat(client, "hi")
    assert forget(client).status_code == 204
    assert forget(client).status_code == 404


def test_delete_is_scoped_to_the_tenant(make_client):
    client = make_client(scripted(ai("ok")))
    chat(client, "hi", tenant="acme")
    assert forget(client, tenant="globex").status_code == 404
    assert history(client, tenant="acme").status_code == 200


def test_session_endpoints_reject_overlong_tenant(make_client):
    client = make_client(scripted())
    assert history(client, tenant="t" * 129).status_code == 422
    assert forget(client, tenant="t" * 129).status_code == 422


# ------------------------------------------------------------------ store


def test_session_store_is_replaceable(make_client):
    class RecordingStore(InMemorySessionStore):
        def __init__(self):
            super().__init__()
            self.appended = []

        async def append(self, tenant_id, session_id, messages):
            self.appended.append((tenant_id, session_id, [m.role for m in messages]))
            await super().append(tenant_id, session_id, messages)

    client = make_client(scripted(ai("ok")))
    client.app.state.session_store = store = RecordingStore()
    chat(client, "hi", tenant="acme", session_id="x")
    assert store.appended == [("acme", "x", ["user", "assistant"])]
    assert history(client, "x", tenant="acme").status_code == 200


def test_in_memory_session_store():
    asyncio.run(_check_session_store())


async def _check_session_store():
    store = InMemorySessionStore()
    assert await store.get("t", "s") is None
    msg = ChatMessage(role="user", content="hi")
    await store.append("t", "s", [msg])
    msg.content = "mutated"
    (await store.get("t", "s"))[0].content = "mutated"
    assert await store.get("t", "s") == [ChatMessage(role="user", content="hi")]
    # Tenant and session are separate key parts, so no concatenation collisions.
    await store.append("t:s", "x", [msg])
    assert await store.get("t", "s:x") is None
    assert await store.delete("t", "s") is True
    assert await store.delete("t", "s") is False
    assert await store.get("t", "s") is None


@pytest.mark.parametrize("role", ["system", "tool", ""])
def test_stored_roles_are_user_or_assistant(role):
    with pytest.raises(ValueError):
        ChatMessage(role=role, content="x")
