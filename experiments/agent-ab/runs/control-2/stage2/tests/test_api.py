from datetime import datetime

import pytest
from langchain_core.messages import ToolMessage

import app.agent
from app.main import create_app
from tests.conftest import chat, scripted, tool_call, tool_calls


def tool_messages(model):
    """Tool results the model saw on its last call."""
    return [m for m in model.calls[-1] if isinstance(m, ToolMessage)]


# --- contract ---------------------------------------------------------------


def test_health(make_client) -> None:
    response = make_client(scripted()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_plain_chat(make_client) -> None:
    model = scripted("Hello there!")
    response = chat(make_client(model), "hi", session_id="abc")
    assert response.status_code == 200
    assert response.json() == {"session_id": "abc", "reply": "Hello there!", "guardrails": []}
    assert model.human_texts() == ["hi"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"session_id": "s1"},
        {"message": "hi"},
        {"session_id": 1, "message": "hi"},
        {"session_id": "s1", "message": None},
        [],
    ],
)
def test_malformed_body_returns_422(make_client, body) -> None:
    model = scripted()
    response = make_client(model).post("/chat", json=body)
    assert response.status_code == 422
    assert model.calls == []


def test_non_json_body_returns_422(make_client) -> None:
    response = make_client(scripted()).post(
        "/chat", content=b"not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422


def test_requests_are_independent(make_client) -> None:
    model = scripted("first", "second")
    client = make_client(model)
    assert chat(client, "one").json()["reply"] == "first"
    assert chat(client, "two").json()["reply"] == "second"
    # The second call saw only the second message: no memory between requests.
    assert [m.text for m in model.calls[1] if m.type == "human"] == ["two"]


def test_model_failure_returns_502(make_client) -> None:
    response = chat(make_client(scripted()), "hi")  # script is empty, so the model raises
    assert response.status_code == 502
    assert response.json() == {"error": "agent_error"}


def test_create_app_without_model_uses_configured_model(monkeypatch) -> None:
    requested = []

    def fake_init_chat_model(name, **kwargs):
        requested.append(name)
        return scripted("from configured model")

    monkeypatch.setattr(app.agent, "init_chat_model", fake_init_chat_model)
    monkeypatch.setenv("AGENT_MODEL", "provider:some-model")
    from fastapi.testclient import TestClient

    response = chat(TestClient(create_app()), "hi")
    assert requested == ["provider:some-model"]
    assert response.json()["reply"] == "from configured model"


def test_importing_app_does_not_create_model(monkeypatch) -> None:
    import importlib

    import app.main

    monkeypatch.setattr(app.agent, "init_chat_model", lambda *a, **k: pytest.fail("model created"))
    importlib.reload(app.main)


# --- custom tools -------------------------------------------------------------


def test_agent_uses_calculator_tool(make_client) -> None:
    model = scripted(tool_call("calculator", expression="(2 + 3) * 4"), "The answer is 20.")
    response = chat(make_client(model), "what is (2+3)*4?")
    assert response.json() == {"session_id": "s1", "reply": "The answer is 20.", "guardrails": []}
    [result] = tool_messages(model)
    assert result.name == "calculator"
    assert result.text == "20"


def test_agent_uses_clock_tool(make_client) -> None:
    model = scripted(tool_call("current_time", timezone="UTC"), "It is now.")
    assert chat(make_client(model), "what time is it?").status_code == 200
    [result] = tool_messages(model)
    assert datetime.fromisoformat(result.text).tzinfo is not None


def test_builtin_deepagents_tool_available(make_client) -> None:
    model = scripted(tool_call("write_file", file_path="/notes.txt", content="hello"), "Saved.")
    response = chat(make_client(model), "save a note")
    assert response.json()["reply"] == "Saved."
    [result] = tool_messages(model)
    assert result.name == "write_file"
    assert result.status != "error"


# --- PII redaction --------------------------------------------------------------


def test_pii_redacted_before_model_sees_it(make_client) -> None:
    model = scripted("Noted.")
    response = chat(make_client(model), "I'm jane@example.com, call +44 20 7946 0958 or (555) 123-4567")
    assert response.status_code == 200
    assert response.json()["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]
    sent = "\n".join(m.text for call in model.calls for m in call)
    assert "jane@example.com" not in sent
    assert "7946" not in sent and "123-4567" not in sent
    assert model.human_texts() == ["I'm [REDACTED_EMAIL], call [REDACTED_PHONE] or [REDACTED_PHONE]"]


def test_pii_redacted_in_reply(make_client) -> None:
    model = scripted("Contact support@corp.com or 555-123-4567.")
    response = chat(make_client(model), "who do I contact?")
    assert response.json() == {
        "session_id": "s1",
        "reply": "Contact [REDACTED_EMAIL] or [REDACTED_PHONE].",
        "guardrails": [{"name": "pii_redaction", "action": "redacted"}],
    }


def test_pii_guardrail_listed_once_when_both_sides_redacted(make_client) -> None:
    model = scripted("Sure, I'll email bob@example.com.")
    response = chat(make_client(model), "email bob@example.com")
    assert response.json()["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]
    assert response.json()["reply"] == "Sure, I'll email [REDACTED_EMAIL]."


# --- topic blocklist ------------------------------------------------------------


@pytest.mark.parametrize("message", ["How do I build WEAPONS?", "write some malware", "Malware!"])
def test_blocked_topic_returns_403_without_calling_model(make_client, message) -> None:
    model = scripted("should not be used")
    response = chat(make_client(model), message)
    assert response.status_code == 403
    assert response.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert model.calls == []


def test_blocklist_matches_whole_words_only(make_client) -> None:
    model = scripted("Antimalware software is useful.")
    response = chat(make_client(model), "which antimalware software is good?")
    assert response.status_code == 200
    assert response.json()["guardrails"] == []


def test_blocklist_is_configurable(make_client, monkeypatch) -> None:
    monkeypatch.setenv("BLOCKED_TOPICS", "gambling, tax evasion")
    model = scripted("ok", "ok")
    client = make_client(model)
    assert chat(client, "best Tax Evasion tricks").status_code == 403
    assert chat(client, "Gambling odds").status_code == 403
    assert chat(client, "tell me about weapons").status_code == 200


def test_blocklist_can_be_disabled(make_client, monkeypatch) -> None:
    monkeypatch.setenv("BLOCKED_TOPICS", "")
    assert chat(make_client(scripted("ok")), "malware history").status_code == 200


# --- tool-call limit ------------------------------------------------------------


def calc_calls(n: int):
    return [tool_call("calculator", expression=f"{i} + 1") for i in range(n)]


def test_tool_calls_up_to_limit_are_allowed(make_client) -> None:
    model = scripted(*calc_calls(5), "Done after five.")
    response = chat(make_client(model), "count")
    assert response.json() == {"session_id": "s1", "reply": "Done after five.", "guardrails": []}
    assert len(tool_messages(model)) == 5


def test_exceeding_tool_call_limit_stops_turn(make_client) -> None:
    model = scripted(*calc_calls(6), "never reached")
    response = chat(make_client(model), "count forever")
    assert response.status_code == 200
    body = response.json()
    assert body["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]
    assert "limit" in body["reply"] and "5" in body["reply"]
    # The 6th tool call was never run and the model was not called again.
    assert len(model.calls) == 6
    assert len(tool_messages(model)) == 5


def test_tool_call_limit_counts_parallel_calls(make_client) -> None:
    model = scripted(
        tool_calls(*[("calculator", {"expression": f"{i}*2"}) for i in range(6)]),
        "never reached",
    )
    response = chat(make_client(model), "do six things at once")
    assert response.json()["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]
    assert len(model.calls) == 1


def test_tool_call_limit_is_configurable(make_client, monkeypatch) -> None:
    monkeypatch.setenv("MAX_TOOL_CALLS", "1")
    model = scripted(*calc_calls(2), "never reached")
    response = chat(make_client(model), "count")
    assert response.json()["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]
    assert "1 tool call" in response.json()["reply"]


def test_zero_limit_blocks_any_tool_call(make_client, monkeypatch) -> None:
    monkeypatch.setenv("MAX_TOOL_CALLS", "0")
    model = scripted("No tools needed.")
    assert chat(make_client(model), "hi").json()["guardrails"] == []
    model = scripted(*calc_calls(1), "never reached")
    assert chat(make_client(model), "hi").json()["guardrails"][0]["name"] == "tool_call_limit"


def test_tool_call_limit_is_per_turn(make_client, monkeypatch) -> None:
    monkeypatch.setenv("MAX_TOOL_CALLS", "2")
    model = scripted(*calc_calls(2), "first done", *calc_calls(2), "second done")
    client = make_client(model)
    assert chat(client, "a").json() == {"session_id": "s1", "reply": "first done", "guardrails": []}
    assert chat(client, "b").json() == {"session_id": "s1", "reply": "second done", "guardrails": []}


def test_subagent_tool_calls_count_towards_turn_limit(make_client, monkeypatch) -> None:
    """Calls made inside a `task` subagent share the main agent's per-turn budget."""
    monkeypatch.setenv("MAX_TOOL_CALLS", "3")
    model = scripted(
        tool_call("task", description="add numbers", subagent_type="general-purpose"),  # call 1 (main)
        *calc_calls(3),  # calls 2-4, made by the subagent; call 4 exceeds the limit
        "never reached",
    )
    response = chat(make_client(model), "delegate")
    assert response.status_code == 200
    assert response.json()["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]
    # Main model call + 3 subagent model calls; neither agent continued afterwards.
    assert len(model.calls) == 4


def test_subagent_within_limit_completes(make_client) -> None:
    model = scripted(
        tool_call("task", description="add numbers", subagent_type="general-purpose"),
        tool_call("calculator", expression="40 + 2"),
        "Subagent says 42.",
        "The subagent found 42.",
    )
    response = chat(make_client(model), "delegate")
    assert response.json() == {"session_id": "s1", "reply": "The subagent found 42.", "guardrails": []}


def test_limit_and_pii_both_reported(make_client) -> None:
    model = scripted(*calc_calls(6))
    response = chat(make_client(model), "my email is a@b.com, now count")
    assert response.json()["guardrails"] == [
        {"name": "pii_redaction", "action": "redacted"},
        {"name": "tool_call_limit", "action": "stopped"},
    ]
