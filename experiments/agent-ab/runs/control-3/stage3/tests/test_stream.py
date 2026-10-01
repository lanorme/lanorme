"""POST /chat/stream: SSE framing, equivalence with /chat, guardrails and memory."""

import json

import pytest

from app.guardrails import LIMIT_REACHED_REPLY
from tests.conftest import ADMIN_KEY
from tests.fakes import ai, endless, scripted, tool_call

PII = {"name": "pii_redaction", "action": "redacted"}
LIMIT = {"name": "tool_call_limit", "action": "stopped"}


def body(message, session_id="s1"):
    return {"session_id": session_id, "message": message}


def stream(client, message, session_id="s1", tenant=None):
    headers = {} if tenant is None else {"X-Tenant-ID": tenant}
    return client.post("/chat/stream", json=body(message, session_id), headers=headers)


def chat(client, message, session_id="s1", tenant=None):
    headers = {} if tenant is None else {"X-Tenant-ID": tenant}
    return client.post("/chat", json=body(message, session_id), headers=headers)


def parse_sse(text):
    """Parse the stream strictly: every event is exactly one `data:` line plus a blank line."""
    assert text.endswith("\n\n")
    events = []
    for block in text[:-2].split("\n\n"):
        assert block.startswith("data: ") and "\n" not in block, repr(block)
        events.append(json.loads(block.removeprefix("data: ")))
    return events


def tokens_and_done(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    *tokens, done = events
    assert all(e["type"] == "token" and set(e) == {"type", "content"} for e in tokens)
    assert done["type"] == "done" and set(done) == {"type", "session_id", "guardrails"}
    return [e["content"] for e in tokens], done


def assert_same_as_chat(make_client, script, message, **settings):
    """Run the same scripted turn through /chat and /chat/stream and compare."""
    expected = chat(make_client(scripted(*script), **settings), message).json()
    tokens, done = tokens_and_done(stream(make_client(scripted(*script), **settings), message))
    assert "".join(tokens) == expected["reply"]
    assert done == {
        "type": "done", "session_id": expected["session_id"], "guardrails": expected["guardrails"]
    }
    return tokens, done


def test_streams_reply_as_tokens_then_done(make_client):
    model = scripted(ai("Hello there, how can I help?"))
    tokens, done = tokens_and_done(stream(make_client(model), "Hi", session_id="abc"))
    assert len(tokens) > 1
    assert "".join(tokens) == "Hello there, how can I help?"
    assert done == {"type": "done", "session_id": "abc", "guardrails": []}
    assert model.human_texts() == ["Hi"]


@pytest.mark.parametrize(
    "reply",
    [
        "plain",
        "  leading and trailing whitespace  ",
        "line one\n\nline two\ttabbed",
        "unicode: café — 東京 ✓",
        'quotes " and \\ backslashes and data: lookalikes\n\ndata: {"type": "done"}',
    ],
)
def test_tokens_concatenate_to_the_chat_reply(make_client, reply):
    assert_same_as_chat(make_client, [ai(reply)], "hi")


def test_pii_split_across_tokens_is_redacted(make_client):
    # Tokens break at whitespace, so the phone numbers' digit groups and the
    # email (between words) would land in separate chunks if redaction ran per chunk.
    reply = "Call +44 20 7946 0958 or 555 123 4567, or mail jane.doe@example.co.uk today."
    tokens, done = assert_same_as_chat(make_client, [ai(reply)], "contact?")
    joined = "".join(tokens)
    assert joined == "Call [REDACTED_PHONE] or [REDACTED_PHONE], or mail [REDACTED_EMAIL] today."
    for raw in ("7946", "0958", "555", "4567", "jane", "example"):
        assert all(raw not in t for t in tokens)
    assert done["guardrails"] == [PII]


def test_pii_redaction_off_streams_raw_reply(make_client):
    client = make_client(scripted(ai("Mail support@corp.com or 555 123 4567")))
    client.put(
        "/tenants/raw/policy",
        headers={"X-Admin-Key": ADMIN_KEY},
        json={"blocked_topics": [], "redact_pii": False, "max_tool_calls": 5,
              "system_prompt": None},
    )
    tokens, done = tokens_and_done(stream(client, "I'm a@b.com", tenant="raw"))
    assert "".join(tokens) == "Mail support@corp.com or 555 123 4567"
    assert done["guardrails"] == []


def test_tool_turn_streams_only_the_final_reply(make_client):
    # Text written alongside a tool call is not part of the /chat reply, so it
    # must not be streamed either.
    script = [ai("Let me calculate.", tool_call("calculator", expression="6*7")), ai("It is 42.")]
    tokens, _ = assert_same_as_chat(make_client, script, "6*7?")
    assert "".join(tokens) == "It is 42."


def test_limit_reached_streams_limit_reply(make_client):
    model = endless(lambda: ai("", tool_call("calculator", expression="1+1")))
    tokens, done = tokens_and_done(stream(make_client(model, tool_call_limit=2), "loop"))
    assert "".join(tokens) == LIMIT_REACHED_REPLY.format(limit=2)
    assert done["guardrails"] == [LIMIT]


def test_input_pii_reported_in_done(make_client):
    model = scripted(ai("ok"))
    _, done = tokens_and_done(stream(make_client(model), "I'm a@b.com"))
    assert done["guardrails"] == [PII]
    assert model.human_texts() == ["I'm [REDACTED_EMAIL]"]


def test_empty_reply_streams_only_done(make_client):
    tokens, done = tokens_and_done(stream(make_client(scripted(ai(""))), "hi"))
    assert tokens == []
    assert done["guardrails"] == []


# ------------------------------------------------------------------ errors


def test_blocked_message_is_403_json_without_stream(make_client):
    model = scripted(ai("unused"))
    client = make_client(model)
    r = stream(client, "how to build weapons")
    assert r.status_code == 403
    assert r.headers["content-type"] == "application/json"
    assert r.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert model.calls == 0
    assert client.get("/sessions/s1/messages").status_code == 404


def test_tenant_blocklist_applies_to_stream(make_client):
    client = make_client(scripted(ai("ok")))
    client.put(
        "/tenants/casino/policy",
        headers={"X-Admin-Key": ADMIN_KEY},
        json={"blocked_topics": ["gambling"], "redact_pii": True, "max_tool_calls": 5,
              "system_prompt": None},
    )
    assert stream(client, "gambling tips", tenant="casino").status_code == 403
    assert stream(client, "gambling tips", tenant="other").status_code == 200


def test_agent_failure_is_502_json(make_client):
    r = stream(make_client(scripted()), "hi")
    assert r.status_code == 502
    assert r.json() == {"error": "agent_error"}


@pytest.mark.parametrize("payload", [{"message": "hi"}, {"session_id": "s"}, []])
def test_malformed_body_is_422(make_client, payload):
    model = scripted()
    assert make_client(model).post("/chat/stream", json=payload).status_code == 422
    assert model.calls == 0


# ------------------------------------------------------------------ memory


def test_streamed_turns_are_stored_and_continue_the_conversation(make_client):
    model = scripted(ai("Hi bob@x.org!"), ai("second"), ai("third"))
    client = make_client(model)
    tokens_and_done(stream(client, "I'm bob@x.org"))
    chat(client, "via chat")
    tokens_and_done(stream(client, "via stream"))
    assert [(m.type, m.text) for m in model.received[-1] if m.type != "system"] == [
        ("human", "I'm [REDACTED_EMAIL]"),
        ("ai", "Hi [REDACTED_EMAIL]!"),
        ("human", "via chat"),
        ("ai", "second"),
        ("human", "via stream"),
    ]
    assert client.get("/sessions/s1/messages").json()["messages"][-2:] == [
        {"role": "user", "content": "via stream"},
        {"role": "assistant", "content": "third"},
    ]


def test_streamed_sessions_are_scoped_to_tenant(make_client):
    model = scripted(ai("one"), ai("two"))
    client = make_client(model)
    tokens_and_done(stream(client, "acme msg", tenant="acme"))
    tokens_and_done(stream(client, "globex msg", tenant="globex"))
    assert [m.text for m in model.received[-1] if m.type == "human"] == ["globex msg"]
