import pytest

from app.tools import CalculationError, calculator, current_time, evaluate_arithmetic


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("1 + 2", 3), ("(2 + 3) * 4", 20), ("7 / 2", 3.5), ("7 // 2", 3), ("-2 ** 2", -4),
     ("2 ** 10", 1024), ("10 % 3", 1), ("1.5 * 2", 3.0)],
)
def test_evaluates_arithmetic(expression: str, expected: float) -> None:
    assert evaluate_arithmetic(expression) == expected


@pytest.mark.parametrize(
    "expression",
    ["__import__('os')", "x + 1", "True + 1", "'a' * 3", "1 / 0", "2 ** 100000", "1 +"],
)
def test_rejects_unsafe_or_invalid_input(expression: str) -> None:
    with pytest.raises(CalculationError):
        evaluate_arithmetic(expression)


def test_calculator_tool_reports_errors_as_text() -> None:
    assert calculator.invoke({"expression": "1 / 0"}).startswith("Error:")
    assert calculator.invoke({"expression": "6 * 7"}) == "42"


def test_current_time_is_iso_utc() -> None:
    assert current_time.invoke({}).endswith("+00:00")
