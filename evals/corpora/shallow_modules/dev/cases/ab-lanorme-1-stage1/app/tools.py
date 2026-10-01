"""Custom tools given to the agent alongside the deepagents built-ins."""

import ast
import operator
from collections.abc import Callable
from datetime import UTC, datetime

from langchain_core.tools import tool

_BINARY: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
# Caps the exponent so "9 ** 9 ** 9" cannot stall the worker.
_MAX_EXPONENT = 1000


class CalculationError(ValueError):
    """The expression is not plain arithmetic, or cannot be evaluated."""


def evaluate_arithmetic(expression: str) -> float:
    """Evaluate ``+ - * / // % **`` over numbers by walking the AST, never ``eval``."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise CalculationError(f"not an arithmetic expression: {expression!r}") from error
    return _evaluate_node(tree.body)


def _evaluate_node(node: ast.expr) -> float:
    """Recursively evaluate one node of an arithmetic expression."""
    match node:
        case ast.Constant(value=int() | float() as number) if not isinstance(number, bool):
            return number
        case ast.UnaryOp(op=op, operand=operand) if type(op) in _UNARY:
            return _UNARY[type(op)](_evaluate_node(operand))
        case ast.BinOp(left=left, op=op, right=right) if type(op) in _BINARY:
            lhs, rhs = _evaluate_node(left), _evaluate_node(right)
            if isinstance(op, ast.Pow) and abs(rhs) > _MAX_EXPONENT:
                raise CalculationError("exponent too large")
            try:
                return _BINARY[type(op)](lhs, rhs)
            except (ZeroDivisionError, OverflowError) as error:
                raise CalculationError(str(error)) from error
        case _:
            raise CalculationError(f"unsupported syntax: {ast.unparse(node)!r}")


@tool
def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression such as "(2 + 3) * 4 / 7" or "2 ** 10".

    Supports + - * / // % ** and parentheses. Use it for any arithmetic rather
    than computing in your head.
    """
    try:
        return str(evaluate_arithmetic(expression))
    except CalculationError as error:
        return f"Error: {error}"


@tool
def current_time() -> str:
    """Return the current date and time in UTC, in ISO 8601 format."""
    return datetime.now(UTC).isoformat(timespec="seconds")


AGENT_TOOLS = (calculator, current_time)
