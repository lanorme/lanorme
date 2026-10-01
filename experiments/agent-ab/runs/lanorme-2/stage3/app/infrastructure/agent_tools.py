"""Tools the agent gets on top of the deepagents built-ins."""

import ast
import operator
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import BaseTool, tool

_MAX_EXPRESSION_LENGTH = 200
# Caps exponents so "9 ** 9 ** 9" cannot pin the CPU.
_MAX_EXPONENT = 100

type _Number = int | float

_BINARY_OPERATORS: dict[type[ast.operator], Callable[[_Number, _Number], _Number]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[_Number], _Number]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class CalculationError(ValueError):
    """The expression is not plain arithmetic, or cannot be evaluated."""


@tool
def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression such as "(3 + 4) * 2 / 7".

    Supports + - * / // % ** and parentheses on integers and decimals.
    """
    try:
        return str(evaluate_arithmetic(expression))
    except CalculationError as exc:
        return f"Error: {exc}"


@tool
def current_time(timezone: str = "UTC") -> str:
    """Return the current date and time as ISO 8601 in an IANA timezone like "Europe/London"."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return f"Error: unknown timezone {timezone!r}"
    return datetime.now(UTC).astimezone(zone).isoformat(timespec="seconds")


AGENT_TOOLS: tuple[BaseTool, ...] = (calculator, current_time)


def evaluate_arithmetic(expression: str) -> _Number:
    """Evaluate arithmetic by walking its syntax tree, never calling eval.

    Raises CalculationError for anything other than numbers and arithmetic.
    """
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise CalculationError(f"expression longer than {_MAX_EXPRESSION_LENGTH} characters")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculationError("not a valid arithmetic expression") from exc
    try:
        return _evaluate_node(tree.body)
    except (ZeroDivisionError, OverflowError) as exc:
        raise CalculationError(str(exc)) from exc


def _evaluate_node(node: ast.expr) -> _Number:
    match node:
        case ast.Constant(value=bool()):
            raise CalculationError("booleans are not numbers here")
        case ast.Constant(value=int() | float() as value):
            return value
        case ast.UnaryOp(op=op, operand=operand) if type(op) in _UNARY_OPERATORS:
            return _UNARY_OPERATORS[type(op)](_evaluate_node(operand))
        case ast.BinOp(left=left, op=op, right=right) if type(op) in _BINARY_OPERATORS:
            return _apply_binary(op=op, left=_evaluate_node(left), right=_evaluate_node(right))
        case _:
            raise CalculationError(f"unsupported syntax: {type(node).__name__}")


def _apply_binary(*, op: ast.operator, left: _Number, right: _Number) -> _Number:
    if isinstance(op, ast.Pow) and abs(right) > _MAX_EXPONENT:
        raise CalculationError(f"exponent larger than {_MAX_EXPONENT}")
    return _BINARY_OPERATORS[type(op)](left, right)
