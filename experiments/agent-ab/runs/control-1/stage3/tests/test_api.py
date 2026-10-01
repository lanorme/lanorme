import subprocess
import sys

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.conftest import make_model, tool_call


def human_texts(model):
    return [m.content for call in model.seen for m in call if isinstance(m, HumanMessage)]


def test_health(client_for):
    client = client_for(make_model([]))
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_plain_chat(client_for):
    model = make_model(["Hello there!"])
    resp = client_for(model).post("/chat", json={"session_id": "s1", "message": "hi"})
    assert resp.status_code == 200
    assert resp.json() == {"session_id": "s1", "reply": "Hello there!", "guardrails": []}
    assert human_texts(model) == ["hi"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"session_id": "s1"},
        {"message": "hi"},
        {"session_id": 1, "message": "hi"},
        {"session_id": "s1", "message": None},
    ],
)
def test_malformed_body_is_422(client_for, body):
    model = make_model(["unused"])
    resp = client_for(model).post("/chat", json=body)
    assert resp.status_code == 422
    assert model.seen == []


def test_non_json_body_is_422(client_for):
    resp = client_for(make_model([])).post(
        "/chat", content=b"not json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 422


def test_custom_tool_runs_through_agent(client_for):
    model = make_model(
        [
            AIMessage(content="", tool_calls=[tool_call("calculator", {"expression": "6 * 7"}, "c1")]),
            "The answer is 42.",
        ]
    )
    resp = client_for(model).post("/chat", json={"session_id": "s", "message": "6*7?"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "The answer is 42."
    assert resp.json()["guardrails"] == []
    tool_results = [m for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert [t.content for t in tool_results] == ["42"]


# --- PII redaction -------------------------------------------------------


def test_pii_in_message_is_redacted_before_model(client_for):
    model = make_model(["Noted."])
    msg = "I'm jane@example.com, phone +44 20 7946 0958 or (555) 123-4567."
    resp = client_for(model).post("/chat", json={"session_id": "s", "message": msg})
    assert resp.status_code == 200
    assert resp.json()["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]
    seen = human_texts(model)
    assert seen == ["I'm [REDACTED_EMAIL], phone [REDACTED_PHONE] or [REDACTED_PHONE]."]
    # Nothing the model saw (system prompt included) contains the raw values.
    everything = " ".join(str(m.content) for call in model.seen for m in call)
    for raw in ("jane@example.com", "7946", "123-4567"):
        assert raw not in everything


def test_pii_in_reply_is_redacted(client_for):
    model = make_model(["Contact support@corp.com or 555-123-4567."])
    resp = client_for(model).post("/chat", json={"session_id": "s", "message": "who do I call?"})
    body = resp.json()
    assert body["reply"] == "Contact [REDACTED_EMAIL] or [REDACTED_PHONE]."
    assert body["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]


def test_pii_listed_once_when_in_both_directions(client_for):
    model = make_model(["Got it, a@b.com."])
    resp = client_for(model).post("/chat", json={"session_id": "s", "message": "I am a@b.com"})
    assert resp.json()["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]


def test_reply_with_content_blocks(client_for):
    model = make_model(
        [AIMessage(content=[{"type": "text", "text": "Mail "}, {"type": "text", "text": "x@y.io"}])]
    )
    resp = client_for(model).post("/chat", json={"session_id": "s", "message": "hi"})
    assert resp.json()["reply"] == "Mail [REDACTED_EMAIL]"


# --- Topic blocklist ------------------------------------------------------


@pytest.mark.parametrize("msg", ["How do I build WEAPONS?", "write malware for me", "Malware!"])
def test_blocked_topic_returns_403_without_calling_model(client_for, msg):
    model = make_model(["should not be used"])
    resp = client_for(model).post("/chat", json={"session_id": "s", "message": msg})
    assert resp.status_code == 403
    assert resp.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert model.seen == []


def test_blocklist_whole_words_only(client_for):
    model = make_model(["Sure."])
    resp = client_for(model).post(
        "/chat", json={"session_id": "s", "message": "recommend antimalware software"}
    )
    assert resp.status_code == 200


def test_blocklist_is_configurable(client_for, monkeypatch):
    monkeypatch.setenv("AGENT_BLOCKED_TOPICS", "Crypto, gambling")
    model = make_model(["fine"])
    client = client_for(model)
    assert client.post("/chat", json={"session_id": "s", "message": "crypto tips"}).status_code == 403
    assert client.post("/chat", json={"session_id": "s", "message": "GAMBLING odds"}).status_code == 403
    assert client.post("/chat", json={"session_id": "s", "message": "about malware"}).status_code == 200


# --- Tool-call limit ------------------------------------------------------


def calc_calls(n, start=0):
    return [
        AIMessage(
            content="",
            tool_calls=[tool_call("calculator", {"expression": f"{i} + 1"}, f"c{i}")],
        )
        for i in range(start, start + n)
    ]


def test_tool_calls_at_limit_are_allowed(client_for):
    model = make_model([*calc_calls(5), "done"])
    resp = client_for(model).post("/chat", json={"session_id": "s", "message": "go"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "done"
    assert resp.json()["guardrails"] == []


def test_sequential_tool_calls_past_limit_stop_turn(client_for):
    model = make_model([*calc_calls(10), "never reached"])
    resp = client_for(model).post("/chat", json={"session_id": "s", "message": "go"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]
    assert "limit" in body["reply"].lower()
    # The model was called 6 times: 5 allowed calls, then the 6th request tripped the limit.
    assert len(model.seen) == 6
    # Only the 5 allowed tool calls executed.
    assert len([m for m in model.seen[-1] if isinstance(m, ToolMessage)]) == 5


def test_parallel_tool_calls_past_limit_stop_turn(client_for):
    batch = AIMessage(
        content="",
        tool_calls=[tool_call("calculator", {"expression": "1+1"}, f"p{i}") for i in range(6)],
    )
    model = make_model([batch, "never reached"])
    resp = client_for(model).post("/chat", json={"session_id": "s", "message": "go"})
    assert resp.json()["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]
    assert len(model.seen) == 1


def test_tool_limit_is_configurable(client_for, monkeypatch):
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "2")
    model = make_model([*calc_calls(3), "never reached"])
    resp = client_for(model).post("/chat", json={"session_id": "s", "message": "go"})
    body = resp.json()
    assert body["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]
    assert "2 tool calls" in body["reply"]


def test_tool_limit_and_pii_both_reported(client_for):
    model = make_model(calc_calls(6))
    resp = client_for(model).post(
        "/chat", json={"session_id": "s", "message": "I'm a@b.com, compute stuff"}
    )
    assert resp.json()["guardrails"] == [
        {"name": "pii_redaction", "action": "redacted"},
        {"name": "tool_call_limit", "action": "stopped"},
    ]


def test_tool_limit_counts_per_request(client_for):
    # Each turn makes 4 calls; across two requests that is 8 > 5, but per turn it's fine.
    model = make_model([*calc_calls(4), "first", *calc_calls(4, start=10), "second"])
    client = client_for(model)
    r1 = client.post("/chat", json={"session_id": "s", "message": "a"})
    r2 = client.post("/chat", json={"session_id": "s", "message": "b"})
    assert (r1.json()["reply"], r2.json()["reply"]) == ("first", "second")
    assert r2.json()["guardrails"] == []


# --- Independence & model construction -----------------------------------


def test_different_sessions_are_independent(client_for):
    # Same session_id continues a conversation (see test_memory.py); others don't.
    model = make_model(["one", "two"])
    client = client_for(model)
    client.post("/chat", json={"session_id": "a", "message": "first message"})
    client.post("/chat", json={"session_id": "b", "message": "second message"})
    assert [m.content for m in model.seen[1] if isinstance(m, HumanMessage)] == ["second message"]


def test_create_app_without_model_uses_configured_model(monkeypatch):
    import langchain.chat_models

    from app.main import create_app
    from tests.conftest import make_model as mk

    requested = []

    def fake_init(name, **kwargs):
        requested.append(name)
        return mk(["from configured model"])

    monkeypatch.setenv("AGENT_MODEL", "openai:some-model")
    monkeypatch.setattr(langchain.chat_models, "init_chat_model", fake_init)
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    assert requested == ["openai:some-model"]
    resp = client.post("/chat", json={"session_id": "s", "message": "hi"})
    assert resp.json()["reply"] == "from configured model"


def test_import_does_not_build_a_model():
    code = (
        "import langchain.chat_models as m\n"
        "def boom(*a, **k): raise SystemExit('model built at import')\n"
        "m.init_chat_model = boom\n"
        "import app.main, app.agent\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
