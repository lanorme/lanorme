from datetime import datetime

import pytest

from app.infrastructure.agent_tools import (
    CalculationError,
    calculator,
    current_time,
    evaluate_arithmetic,
)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("7 / 2", 3.5),
        ("7 // 2", 3),
        ("7 % 3", 1),
        ("-2 ** 2", -4),
        ("2 ** 10", 1024),
        ("+1.5 - 0.5", 1.0),
    ],
)
def test_evaluates_arithmetic(expression: str, expected: float) -> None:
    assert evaluate_arithmetic(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('ls')",
        "x + 1",
        "True + 1",
        "'a' * 3",
        "1 +",
        "2 ** 1000",
        "1 / 0",
        "1" * 201,
    ],
)
def test_rejects_anything_but_safe_arithmetic(expression: str) -> None:
    with pytest.raises(CalculationError):
        evaluate_arithmetic(expression)


def test_calculator_tool_reports_errors_as_text() -> None:
    assert calculator.invoke({"expression": "6 * 7"}) == "42"
    assert calculator.invoke({"expression": "1 / 0"}).startswith("Error:")


def test_current_time_returns_iso_timestamp_in_the_zone() -> None:
    stamp = datetime.fromisoformat(current_time.invoke({"timezone": "Asia/Tokyo"}))

    assert stamp.utcoffset() is not None
    assert stamp.utcoffset().total_seconds() == 9 * 3600


def test_current_time_rejects_unknown_zone() -> None:
    assert current_time.invoke({"timezone": "Mars/Base"}).startswith("Error:")
