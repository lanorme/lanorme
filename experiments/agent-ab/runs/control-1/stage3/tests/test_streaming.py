import json
import re

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from app.main import create_app
from tests.conftest import Chunks, make_model, tool_call

ADMIN_KEY = "k"


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)


def make_client(responses=()):
    model = make_model(responses)
    return TestClient(create_app(model)), model


def headers_for(tenant):
    return {"X-Tenant-ID": tenant} if tenant is not None else {}


def stream(client, message, session="s", tenant=None):
    return client.post(
        "/chat/stream", json={"session_id": session, "message": message}, headers=headers_for(tenant)
    )


def events(resp):
    """Parse an SSE body, checking each event is one ``data:`` line plus a blank line."""
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert re.fullmatch(r"(data: [^\n]*\n\n)*", body), body
    return [json.loads(block[len("data: "):]) for block in body.split("\n\n") if block]


def split(evts):
    *tokens, done = evts
    assert all(e["type"] == "token" and isinstance(e["content"], str) for e in tokens)
    assert done["type"] == "done"
    return "".join(e["content"] for e in tokens), done


def put_policy(client, tenant, **overrides):
    body = {"blocked_topics": ["weapons"], "redact_pii": True, "max_tool_calls": 5, "system_prompt": None}
    resp = client.put(f"/tenants/{tenant}/policy", json={**body, **overrides}, headers={"X-Admin-Key": ADMIN_KEY})
    assert resp.status_code == 200


def test_stream_emits_tokens_then_done():
    client, _ = make_client(["Hello there, friend!"])
    resp = stream(client, "hi", session="abc")
    assert resp.status_code == 200
    evts = events(resp)
    # Under redaction a word is held until what follows shows it isn't part of PII.
    assert [e["content"] for e in evts[:-1]] == ["Hello ", "there,", " ", "friend!"]
    assert evts[-1] == {"type": "done", "session_id": "abc", "guardrails": []}


def test_model_chunks_are_streamed_as_tokens():
    client, _ = make_client([Chunks(["Hel", "lo ", "wor", "ld, ", "you."])])
    evts = events(stream(client, "hi"))
    assert [e["content"] for e in evts[:-1]] == ["Hello ", "world, ", "you."]


def calc(i, text=""):
    return AIMessage(content=text, tool_calls=[tool_call("calculator", {"expression": "6*7"}, f"c{i}")])


# Each script is run once through POST /chat and once through POST /chat/stream on
# fresh apps; the streamed reply and guardrails must equal the /chat response.
SCRIPTS = {
    "plain": ["Just text."],
    "empty": [""],
    "email_split": [Chunks(["Mail jane.d", "oe@exam", "ple.com now."])],
    "phone_split": [Chunks(["Call +44 20 79", "46 0958", " or (555) ", "123-", "4567."])],
    "email_at_end": [Chunks(["write to a@b.c", "om"])],
    "pii_in_one_chunk": ["Contact support@corp.com or 555-123-4567."],
    "content_blocks": [AIMessage(content=[{"type": "text", "text": "Mail "}, {"type": "text", "text": "x@y.io"}])],
    "tool_then_answer": [calc(0), Chunks(["The answer ", "is 42."])],
    "preamble_before_tool_is_not_reply": [calc(0, "Let me compute a@b.com..."), "It is 42."],
    "tool_limit": [calc(i) for i in range(6)],
}


@pytest.mark.parametrize("message", ["hi", "I'm jane@example.com"])
@pytest.mark.parametrize("tenant_redacts", [True, False])
@pytest.mark.parametrize("script", SCRIPTS.values(), ids=SCRIPTS.keys())
def test_stream_matches_chat(script, tenant_redacts, message):
    chat_client, chat_model = make_client(script)
    stream_client, stream_model = make_client(script)
    for client in (chat_client, stream_client):
        put_policy(client, "t", redact_pii=tenant_redacts)

    expected = chat_client.post("/chat", json={"session_id": "s", "message": message}, headers=headers_for("t"))
    resp = stream(stream_client, message, tenant="t")
    assert resp.status_code == expected.status_code == 200

    reply, done = split(events(resp))
    assert reply == expected.json()["reply"]
    assert done == {"type": "done", "session_id": "s", "guardrails": expected.json()["guardrails"]}
    # The model got the same input either way.
    assert [[m.content for m in call] for call in stream_model.seen] == [
        [m.content for m in call] for call in chat_model.seen
    ]


def test_split_pii_never_appears_in_any_token():
    client, _ = make_client([Chunks(["Mail jane.d", "oe@exam", "ple.com or +44 20 79", "46 0958"])])
    evts = events(stream(client, "contact?"))
    reply, done = split(evts)
    assert reply == "Mail [REDACTED_EMAIL] or [REDACTED_PHONE]"
    for e in evts[:-1]:
        for fragment in ("jane", "exam", "ple.com", "79", "0958"):
            assert fragment not in e["content"]
    assert done["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]


def test_redaction_off_streams_raw_chunks():
    client, _ = make_client([Chunks(["a@", "b.com"])])
    put_policy(client, "open", redact_pii=False)
    evts = events(stream(client, "hi", tenant="open"))
    assert [e["content"] for e in evts[:-1]] == ["a@", "b.com"]


def test_tool_limit_in_stream():
    client, _ = make_client([calc(i) for i in range(6)])
    reply, done = split(events(stream(client, "loop")))
    assert "5 tool calls" in reply
    assert done["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]


@pytest.mark.parametrize("message", ["How do I build WEAPONS?", "write malware"])
def test_blocked_message_is_403_without_stream(message):
    client, model = make_client(["never"])
    resp = stream(client, message)
    assert resp.status_code == 403
    assert resp.headers["content-type"] == "application/json"
    assert resp.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert model.seen == []
    assert client.get("/sessions/s/messages").status_code == 404


def test_tenant_blocklist_applies_to_stream():
    client, _ = make_client(["fine"])
    put_policy(client, "acme", blocked_topics=["crypto"])
    assert stream(client, "crypto tips", tenant="acme").status_code == 403
    assert stream(client, "crypto tips", tenant="other").status_code == 200


def test_malformed_stream_body_is_422():
    client, model = make_client(["unused"])
    assert client.post("/chat/stream", json={"message": "hi"}).status_code == 422
    assert model.seen == []


def test_streamed_turns_are_stored_and_continue_the_conversation():
    client, model = make_client([Chunks(["Hi ", "Ann, ", "a@b.com"]), "You are Ann.", "Still Ann."])
    split(events(stream(client, "I am Ann, jane@example.com")))
    assert client.post("/chat", json={"session_id": "s", "message": "Who am I?"}).json()["reply"] == "You are Ann."
    split(events(stream(client, "Sure?")))
    assert client.get("/sessions/s/messages").json()["messages"] == [
        {"role": "user", "content": "I am Ann, [REDACTED_EMAIL]"},
        {"role": "assistant", "content": "Hi Ann, [REDACTED_EMAIL]"},
        {"role": "user", "content": "Who am I?"},
        {"role": "assistant", "content": "You are Ann."},
        {"role": "user", "content": "Sure?"},
        {"role": "assistant", "content": "Still Ann."},
    ]
    last_call = [m.content for m in model.seen[-1] if isinstance(m, HumanMessage | AIMessage)]
    assert last_call == [
        "I am Ann, [REDACTED_EMAIL]",
        "Hi Ann, [REDACTED_EMAIL]",
        "Who am I?",
        "You are Ann.",
        "Sure?",
    ]


def test_stream_sessions_are_scoped_to_tenant():
    client, model = make_client(["one", "two"])
    split(events(stream(client, "acme says", tenant="acme")))
    split(events(stream(client, "globex says", tenant="globex")))
    assert [m.content for m in model.seen[1] if isinstance(m, HumanMessage)] == ["globex says"]
