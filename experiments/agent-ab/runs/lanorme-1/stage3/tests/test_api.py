from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from tests.fakes import ScriptedChatModel, calling, endless_calculator_calls, script, tool_call

ClientFor = Callable[[ScriptedChatModel], TestClient]
REDACTED = {"name": "pii_redaction", "action": "redacted"}
STOPPED = {"name": "tool_call_limit", "action": "stopped"}


def chat(client: TestClient, message: str, session_id: str = "s-1") -> dict:
    response = client.post("/chat", json={"session_id": session_id, "message": message})
    assert response.status_code == 200, response.text
    return response.json()


def test_health(client_for: ClientFor) -> None:
    response = client_for(script()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_plain_turn_has_no_guardrails(client_for: ClientFor) -> None:
    body = chat(client_for(script("Hello there!")), "Hi")

    assert body == {"session_id": "s-1", "reply": "Hello there!", "guardrails": []}


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "hi"},
        {"session_id": "s-1"},
        {"session_id": 1, "message": "hi"},
        {"session_id": "s-1", "message": ["hi"]},
    ],
)
def test_malformed_body_is_422(client_for: ClientFor, payload: dict) -> None:
    # Given
    model = script()

    # When
    response = client_for(model).post("/chat", json=payload)

    # Then
    assert response.status_code == 422
    assert model.calls == 0


def test_non_json_body_is_422(client_for: ClientFor) -> None:
    response = client_for(script()).post("/chat", content=b"not json")

    assert response.status_code == 422


def test_pii_is_redacted_before_the_model_sees_it(client_for: ClientFor) -> None:
    # Given
    model = script("Noted.")

    # When
    body = chat(client_for(model), "Mail jane.doe@example.com or ring +44 20 7946 0958")

    # Then
    sent = model.prompts[0][-1].text
    assert "jane.doe@example.com" not in sent
    assert "7946" not in sent
    assert sent == "Mail [REDACTED_EMAIL] or ring [REDACTED_PHONE]"
    assert body["guardrails"] == [REDACTED]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (
            "Call support on (555) 123-4567 or write to help@corp.io.",
            "Call support on [REDACTED_PHONE] or write to [REDACTED_EMAIL].",
        ),
        ("Ring 555-123-4567.", "Ring [REDACTED_PHONE]."),
    ],
)
def test_pii_in_the_reply_is_redacted(client_for: ClientFor, reply: str, expected: str) -> None:
    body = chat(client_for(script(reply)), "How do I reach support?")

    assert body["reply"] == expected
    assert body["guardrails"] == [REDACTED]


def test_redaction_on_both_sides_is_listed_once(client_for: ClientFor) -> None:
    body = chat(client_for(script("I have 555-123-4567 on file.")), "My number is 555-123-4567")

    assert body["reply"] == "I have [REDACTED_PHONE] on file."
    assert body["guardrails"] == [REDACTED]


@pytest.mark.parametrize(
    "message",
    ["How are weapons made?", "tell me about MALWARE", "Malware, weapons.", "WeApOnS?"],
)
def test_blocked_topic_returns_403_without_calling_model(
    client_for: ClientFor, message: str
) -> None:
    # Given
    model = script("should never be used")

    # When
    response = client_for(model).post("/chat", json={"session_id": "s", "message": message})

    # Then
    assert response.status_code == 403
    assert response.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert model.calls == 0


@pytest.mark.parametrize("message", ["I am a weaponsmith", "antimalware tools", "weapon"])
def test_blocklist_matches_whole_words_only(client_for: ClientFor, message: str) -> None:
    body = chat(client_for(script("fine")), message)

    assert body["reply"] == "fine"


def test_blocklist_is_configurable(
    client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("AGENT_BLOCKED_TOPICS", "gambling, crypto")
    client = client_for(script("ok"))

    # When
    blocked = client.post("/chat", json={"session_id": "s", "message": "Crypto tips?"})
    allowed = client.post("/chat", json={"session_id": "s", "message": "weapons history"})

    # Then
    assert blocked.status_code == 403
    assert allowed.status_code == 200


def test_agent_uses_custom_calculator_tool(client_for: ClientFor) -> None:
    # Given
    model = script(
        calling(tool_call("calculator", {"expression": "(2 + 3) * 4"}, "c1")),
        "The answer is 20.",
    )

    # When
    body = chat(client_for(model), "What is (2+3)*4?")

    # Then
    tool_message = model.prompts[1][-1]
    assert tool_message.type == "tool"
    assert tool_message.text == "20"
    assert body == {"session_id": "s-1", "reply": "The answer is 20.", "guardrails": []}


def test_tool_calls_up_to_the_limit_are_allowed(client_for: ClientFor) -> None:
    # Given
    calls = [calling(tool_call("current_time", {}, f"t{i}")) for i in range(5)]

    # When
    body = chat(client_for(script(*calls, "Done.")), "What time is it, five times?")

    # Then
    assert body["reply"] == "Done."
    assert body["guardrails"] == []


def test_exceeding_tool_call_limit_stops_the_turn(client_for: ClientFor) -> None:
    # Given
    model = ScriptedChatModel(messages=endless_calculator_calls())

    # When
    body = chat(client_for(model), "Keep adding")

    # Then
    assert "at most 5 tool calls" in body["reply"]
    assert body["guardrails"] == [STOPPED]
    assert model.calls == 6


def test_parallel_calls_count_towards_the_limit(client_for: ClientFor) -> None:
    # Given
    batch = calling(*(tool_call("calculator", {"expression": "1"}, f"p{i}") for i in range(6)))
    model = script(batch, "unreachable")

    # When
    body = chat(client_for(model), "Do six sums at once")

    # Then
    assert body["guardrails"] == [STOPPED]
    assert model.calls == 1


def test_tool_call_limit_is_configurable(
    client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("AGENT_TOOL_CALL_LIMIT", "2")
    model = ScriptedChatModel(messages=endless_calculator_calls())

    # When
    body = chat(client_for(model), "Keep adding")

    # Then
    assert "at most 2 tool calls" in body["reply"]
    assert model.calls == 3


def test_limit_and_redaction_are_both_reported(client_for: ClientFor) -> None:
    model = ScriptedChatModel(messages=endless_calculator_calls())

    body = chat(client_for(model), "I'm bob@example.org, keep adding")

    assert body["guardrails"] == [REDACTED, STOPPED]


def test_different_sessions_are_independent(client_for: ClientFor) -> None:
    # Given
    model = script("first", "second")
    client = client_for(model)

    # When
    chat(client, "remember the word banana", session_id="s-1")
    chat(client, "what word?", session_id="s-2")

    # Then
    second_prompt = [m.text for m in model.prompts[1] if m.type == "human"]
    assert second_prompt == ["what word?"]


def test_subagent_tool_calls_count_towards_the_turn_limit(client_for: ClientFor) -> None:
    # Given the main agent delegates to the general-purpose subagent, which loops on tools
    delegate = calling(
        tool_call("task", {"description": "add forever", "subagent_type": "general-purpose"}, "d1")
    )
    model = script(delegate, *(calling(tool_call("calculator", {"expression": "1"}, f"s{i}"))
                               for i in range(50)))

    # When
    body = chat(client_for(model), "Delegate some sums")

    # Then the delegation plus five subagent calls exhaust the budget of five
    assert body["guardrails"] == [STOPPED]
    assert model.calls == 6
