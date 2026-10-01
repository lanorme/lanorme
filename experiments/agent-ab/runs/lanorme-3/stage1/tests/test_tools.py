"""The service's own tools, alone and when the agent calls them."""

from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.tools import CalculationError, calculator, current_utc_time, evaluate_arithmetic
from tests.fakes import ClientFactory, ScriptedChatModel, make_tool_call


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("1 + 2 * 3", 7), ("(1 + 2) * 3", 9), ("7 / 2", 3.5), ("7 // 2", 3), ("7 % 4", 3), ("2 ** 10", 1024),
     ("-3 + +5", 2), ("0.1 * 10", 1.0)],
)
def test_calculator_evaluates_arithmetic(expression: str, expected: float) -> None:
    assert evaluate_arithmetic(expression) == expected


@pytest.mark.parametrize(
    "expression",
    ["__import__('os')", "x + 1", "len('abc')", "(1).real", "True + 1", "'a' * 3", "1 +", "1 / 0", "9 ** 9 ** 9",
     "1" * 201],
)
def test_calculator_rejects_unsafe_or_invalid_input(expression: str) -> None:
    with pytest.raises(CalculationError):
        evaluate_arithmetic(expression)


def test_calculator_tool_reports_errors_as_text() -> None:
    assert calculator.invoke({"expression": "1 / 0"}).startswith("Error:")


def test_clock_returns_current_utc_time() -> None:
    reported = datetime.fromisoformat(current_utc_time.invoke({}))

    assert reported.utcoffset() == timedelta(0)
    assert abs(datetime.now(UTC) - reported) < timedelta(seconds=5)


def test_agent_feeds_tool_results_back_to_the_model(client_for: ClientFactory) -> None:
    # Given
    script = [make_tool_call("calculator", {"expression": "6 * 7"}, "calc-1"), AIMessage(content="It is 42.")]
    model = ScriptedChatModel(messages=iter(script))

    # When
    response = client_for(model).post("/chat", json={"session_id": "s1", "message": "What is 6 times 7?"})

    # Then
    assert response.json() == {"session_id": "s1", "reply": "It is 42.", "guardrails": []}
    tool_results = [message for message in model.received[-1] if isinstance(message, ToolMessage)]
    assert [result.text for result in tool_results] == ["42"]
