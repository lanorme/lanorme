from datetime import datetime

import pytest

from app.tools import calculator, current_time


@pytest.mark.parametrize(
    "expr, expected",
    [("2 + 3 * 4", "14"), ("(2 + 3) * 4", "20"), ("sqrt(16)", "4.0"), ("-2 ** 2", "-4"), ("7 // 2", "3")],
)
def test_calculator(expr, expected):
    assert calculator.invoke({"expression": expr}) == expected


@pytest.mark.parametrize(
    "expr", ["__import__('os')", "1/0", "2 ** 100000", "foo + 1", "1 +", "True + 1"]
)
def test_calculator_rejects_bad_input(expr):
    assert calculator.invoke({"expression": expr}).startswith("Error:")


def test_current_time():
    assert datetime.fromisoformat(current_time.invoke({})).utcoffset().total_seconds() == 0
    assert datetime.fromisoformat(current_time.invoke({"timezone_name": "Asia/Tokyo"}))
    assert current_time.invoke({"timezone_name": "Mars/Base"}).startswith("Error:")
