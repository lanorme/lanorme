"""Tool-call limit: a turn may make at most N tool calls, then it stops with a 200."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from tests.fakes import ClientFactory, ScriptedChatModel, make_tool_call, repeat_calculator_calls

LIMIT_ACTION = {"name": "tool_call_limit", "action": "stopped"}


def count_tool_results(model: ScriptedChatModel) -> int:
    return sum(isinstance(message, ToolMessage) for message in model.received[-1])


def test_runaway_agent_is_stopped_at_the_default_limit(client_for: ClientFactory) -> None:
    # Given
    model = ScriptedChatModel(messages=repeat_calculator_calls())

    # When
    response = client_for(model).post("/chat", json={"session_id": "s1", "message": "Count forever"})

    # Then
    assert response.status_code == 200
    assert response.json()["guardrails"] == [LIMIT_ACTION]
    assert "limit of 5 tool calls" in response.json()["reply"]
    assert count_tool_results(model) == 5


def test_limit_is_configurable(client_for: ClientFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    monkeypatch.setenv("MAX_TOOL_CALLS", "2")
    model = ScriptedChatModel(messages=repeat_calculator_calls())

    # When
    response = client_for(model).post("/chat", json={"session_id": "s1", "message": "Count forever"})

    # Then
    assert response.json()["guardrails"] == [LIMIT_ACTION]
    assert "limit of 2 tool calls" in response.json()["reply"]
    assert count_tool_results(model) == 2


def test_calls_up_to_the_limit_are_allowed(client_for: ClientFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    monkeypatch.setenv("MAX_TOOL_CALLS", "2")
    script = [
        make_tool_call("calculator", {"expression": "1 + 1"}, "a"),
        make_tool_call("calculator", {"expression": "2 + 2"}, "b"),
        AIMessage(content="Done: 2 and 4."),
    ]
    model = ScriptedChatModel(messages=iter(script))

    # When
    response = client_for(model).post("/chat", json={"session_id": "s1", "message": "Add twice"})

    # Then
    assert response.json() == {"session_id": "s1", "reply": "Done: 2 and 4.", "guardrails": []}


def test_parallel_calls_count_towards_the_limit(client_for: ClientFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    monkeypatch.setenv("MAX_TOOL_CALLS", "2")
    calls = [{"name": "calculator", "args": {"expression": f"{n} * 2"}, "id": f"p{n}"} for n in range(3)]
    model = ScriptedChatModel(messages=iter([AIMessage(content="", tool_calls=calls), AIMessage(content="never")]))

    # When
    response = client_for(model).post("/chat", json={"session_id": "s1", "message": "Three at once"})

    # Then
    assert response.status_code == 200
    assert response.json()["guardrails"] == [LIMIT_ACTION]
    assert len(model.received) == 1


def test_limit_and_redaction_are_both_reported(client_for: ClientFactory) -> None:
    model = ScriptedChatModel(messages=repeat_calculator_calls())

    response = client_for(model).post("/chat", json={"session_id": "s1", "message": "Text 555-123-4567 forever"})

    assert response.json()["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}, LIMIT_ACTION]
