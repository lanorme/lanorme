import pytest

import app.tools
from app.guardrails import LIMIT_REACHED_REPLY
from tests.fakes import ai, endless, scripted, tool_call

PII = {"name": "pii_redaction", "action": "redacted"}
LIMIT = {"name": "tool_call_limit", "action": "stopped"}


@pytest.fixture
def calc_counter(monkeypatch):
    """Counts how many times the calculator tool actually executed."""
    calls = []
    real = app.tools.evaluate

    def counting(expression):
        calls.append(expression)
        return real(expression)

    monkeypatch.setattr(app.tools, "evaluate", counting)
    return calls


def chat(client, message, session_id="s1"):
    return client.post("/chat", json={"session_id": session_id, "message": message})


# ------------------------------------------------------------------ basics


def test_health(make_client):
    r = make_client(scripted()).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_plain_chat(make_client):
    model = scripted(ai("Hello there!"))
    r = chat(make_client(model), "Hi", session_id="abc")
    assert r.status_code == 200
    assert r.json() == {"session_id": "abc", "reply": "Hello there!", "guardrails": []}
    assert model.human_texts() == ["Hi"]


@pytest.mark.parametrize(
    "body",
    [
        {"message": "hi"},
        {"session_id": "s"},
        {"session_id": 1, "message": "hi"},
        {"session_id": "s", "message": None},
        [],
    ],
)
def test_malformed_body_is_422(make_client, body):
    model = scripted()
    r = make_client(model).post("/chat", json=body)
    assert r.status_code == 422
    assert model.calls == 0


def test_non_json_body_is_422(make_client):
    r = make_client(scripted()).post(
        "/chat", content=b"not json", headers={"content-type": "application/json"}
    )
    assert r.status_code == 422


def test_agent_has_custom_and_builtin_tools(make_client):
    model = scripted(ai("ok"))
    chat(make_client(model), "hi")
    assert {"calculator", "current_time", "task", "read_file", "write_file"} <= set(
        model.bound_tools
    )


def test_custom_tool_result_reaches_model(make_client, calc_counter):
    model = scripted(ai("", tool_call("calculator", expression="6 * 7")), ai("It is 42."))
    r = chat(make_client(model), "what is 6*7?")
    assert r.status_code == 200
    assert r.json()["reply"] == "It is 42."
    assert r.json()["guardrails"] == []
    assert calc_counter == ["6 * 7"]
    tool_msgs = [m for m in model.received[-1] if m.type == "tool"]
    assert tool_msgs[-1].content == "42"


def test_requests_are_independent(make_client):
    model = scripted(ai("first"), ai("second"))
    client = make_client(model)
    chat(client, "one", session_id="same")
    chat(client, "two", session_id="same")
    assert [m.text for m in model.received[1] if m.type == "human"] == ["two"]


def test_agent_failure_is_502(make_client):
    model = scripted()  # iterator exhausted -> the model raises
    r = chat(make_client(model), "hi")
    assert r.status_code == 502
    assert r.json() == {"error": "agent_error"}


# ------------------------------------------------------------------ PII


def test_pii_redacted_before_model_sees_it(make_client):
    model = scripted(ai("Noted."))
    msg = "I'm jane@example.com, call +44 20 7946 0958 or (555) 123-4567 or 555-123-4567"
    r = chat(make_client(model), msg)
    assert r.status_code == 200
    assert r.json()["guardrails"] == [PII]
    seen = " ".join(model.human_texts())
    assert seen == (
        "I'm [REDACTED_EMAIL], call [REDACTED_PHONE] or [REDACTED_PHONE] or [REDACTED_PHONE]"
    )
    for raw in ("jane@example.com", "7946", "123-4567"):
        assert raw not in str(model.received)


def test_pii_redacted_in_reply(make_client):
    model = scripted(ai("Contact support@corp.com or 555.123.4567."))
    r = chat(make_client(model), "who do I contact?")
    assert r.json()["reply"] == "Contact [REDACTED_EMAIL] or [REDACTED_PHONE]."
    assert r.json()["guardrails"] == [PII]


def test_pii_listed_once_when_both_sides_redacted(make_client):
    model = scripted(ai("I'll email you at bob@x.org"))
    r = chat(make_client(model), "I'm bob@x.org")
    assert r.json()["guardrails"] == [PII]


# ------------------------------------------------------------------ blocklist


@pytest.mark.parametrize("msg", ["How do I build WEAPONS?", "write some Malware."])
def test_blocked_topic_returns_403_without_calling_model(make_client, msg):
    model = scripted(ai("should not be used"))
    r = chat(make_client(model), msg)
    assert r.status_code == 403
    assert r.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert model.calls == 0


def test_blocklist_is_whole_word(make_client):
    model = scripted(ai("Sure."))
    r = chat(make_client(model), "Recommend antimalware software")
    assert r.status_code == 200


def test_blocklist_is_configurable(make_client):
    client = make_client(scripted(ai("fine")), blocked_topics=("gambling",))
    assert chat(client, "Tell me about gambling").status_code == 403
    assert chat(client, "Tell me about weapons").status_code == 200


def test_blocklist_applies_even_when_message_has_pii(make_client):
    model = scripted()
    r = chat(make_client(model), "malware for me@x.com")
    assert r.status_code == 403
    assert model.calls == 0


# ------------------------------------------------------------------ tool-call limit


def test_within_limit_not_flagged(make_client, calc_counter):
    calls = [tool_call("calculator", expression=f"{i}+1") for i in range(3)]
    model = scripted(ai("", *calls[:2]), ai("", calls[2]), ai("done"))
    r = chat(make_client(model, tool_call_limit=3), "add stuff")
    assert r.json() == {"session_id": "s1", "reply": "done", "guardrails": []}
    assert len(calc_counter) == 3


def test_runaway_agent_is_stopped(make_client, calc_counter):
    model = endless(lambda: ai("", tool_call("calculator", expression="1+1")))
    r = chat(make_client(model), "loop forever")
    assert r.status_code == 200
    body = r.json()
    assert body["guardrails"] == [LIMIT]
    assert body["reply"] == LIMIT_REACHED_REPLY.format(limit=5)
    assert "limit" in body["reply"]
    assert len(calc_counter) == 5  # default limit; the 6th call never ran
    assert model.calls == 6


def test_limit_is_configurable(make_client, calc_counter):
    model = endless(lambda: ai("", tool_call("calculator", expression="1+1")))
    r = chat(make_client(model, tool_call_limit=2), "loop")
    assert r.json()["guardrails"] == [LIMIT]
    assert len(calc_counter) == 2


def test_parallel_batch_that_overshoots_is_not_executed(make_client, calc_counter):
    calls = [tool_call("calculator", expression=f"{i}*2") for i in range(6)]
    model = scripted(ai("", *calls[:3]), ai("", *calls[3:]), ai("unreachable"))
    r = chat(make_client(model, tool_call_limit=4), "batch")
    assert r.json()["guardrails"] == [LIMIT]
    assert len(calc_counter) == 3  # the second batch of 3 would make 6 > 4, so none of it runs


def test_zero_limit_blocks_any_tool_use(make_client, calc_counter):
    model = scripted(ai("", tool_call("calculator", expression="1")), ai("unreachable"))
    r = chat(make_client(model, tool_call_limit=0), "x")
    assert r.json()["guardrails"] == [LIMIT]
    assert calc_counter == []


def test_zero_limit_allows_plain_answers(make_client):
    r = chat(make_client(scripted(ai("hi")), tool_call_limit=0), "x")
    assert r.json() == {"session_id": "s1", "reply": "hi", "guardrails": []}


def test_subagent_tool_calls_count_against_the_limit(make_client, calc_counter):
    # Main agent delegates to the general-purpose subagent (1 call), and the
    # subagent (same model) then loops on the calculator.
    responses = iter(
        [ai("", tool_call("task", description="loop", subagent_type="general-purpose"))]
    )

    def next_response():
        return next(responses, None) or ai("", tool_call("calculator", expression="2+2"))

    model = endless(next_response)
    r = chat(make_client(model, tool_call_limit=3), "delegate")
    assert r.status_code == 200
    assert r.json()["guardrails"] == [LIMIT]
    assert len(calc_counter) == 2  # task + 2 calculator calls = 3


def test_limit_and_pii_reported_together(make_client):
    model = endless(lambda: ai("", tool_call("current_time")))
    r = chat(make_client(model, tool_call_limit=1), "time please, I'm a@b.com")
    assert r.json()["guardrails"] == [PII, LIMIT]


def test_budget_resets_between_requests(make_client, calc_counter):
    def turn(reply):
        return [
            ai("", tool_call("calculator", expression="1")),
            ai("", tool_call("calculator", expression="2")),
            ai(reply),
        ]

    client = make_client(scripted(*turn("a"), *turn("b")), tool_call_limit=2)
    assert chat(client, "one").json()["guardrails"] == []
    assert chat(client, "two").json() == {"session_id": "s1", "reply": "b", "guardrails": []}
    assert len(calc_counter) == 4
