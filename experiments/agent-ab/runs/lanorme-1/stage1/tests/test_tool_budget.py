import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.guardrails.tool_budget import (
    ToolCallBudgetMiddleware,
    ToolCallLimitExceededError,
    open_turn,
)


def requesting(count: int) -> dict:
    calls = [{"name": "calculator", "args": {}, "id": f"c{i}"} for i in range(count)]
    return {"messages": [AIMessage(content="", tool_calls=calls)]}


def test_counts_accumulate_across_model_calls_in_a_turn() -> None:
    # Given
    budget = ToolCallBudgetMiddleware(limit=3)

    # When
    with open_turn() as calls:
        budget.after_model(requesting(2), runtime=None)
        with pytest.raises(ToolCallLimitExceededError):
            budget.after_model(requesting(2), runtime=None)

    # Then
    assert calls.requested == 4
    assert calls.exceeded


def test_each_turn_starts_from_zero() -> None:
    # Given a turn that used the whole budget
    budget = ToolCallBudgetMiddleware(limit=2)
    with open_turn():
        budget.after_model(requesting(2), runtime=None)

    # When the next turn spends it again
    with open_turn() as calls:
        budget.after_model(requesting(2), runtime=None)

    # Then
    assert not calls.exceeded


def test_non_ai_messages_are_ignored() -> None:
    budget = ToolCallBudgetMiddleware(limit=0)

    with open_turn() as calls:
        budget.after_model({"messages": [HumanMessage(content="hi")]}, runtime=None)

    assert calls.requested == 0


def test_refuses_to_run_outside_a_turn() -> None:
    with pytest.raises(RuntimeError):
        ToolCallBudgetMiddleware(limit=1).after_model(requesting(1), runtime=None)
