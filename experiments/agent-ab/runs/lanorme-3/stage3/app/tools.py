"""Tools the agent can call in addition to the deepagents built-ins."""

import ast
import operator
from collections.abc import Callable
from datetime import UTC, datetime

from langchain_core.tools import BaseTool, tool

type Number = int | float

BINARY_OPERATORS: dict[type[ast.operator], Callable[[Number, Number], Number]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[Number], Number]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
# Powers are bounded so a request such as 9**9**9 cannot pin the CPU or memory.
MAX_RESULT_BITS = 4096
MAX_EXPRESSION_LENGTH = 200


class CalculationError(ValueError):
    """The expression is not plain arithmetic, or cannot be evaluated safely."""


def evaluate_arithmetic(expression: str) -> Number:
    """Evaluate ``+ - * / // % **`` and parentheses over numeric literals.

    The expression is parsed, never executed, so names, calls and attribute
    access are rejected rather than run.
    """
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalculationError(f"expression is longer than {MAX_EXPRESSION_LENGTH} characters")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise CalculationError(f"not a valid expression: {expression!r}") from error
    return evaluate_node(tree.body)


def evaluate_node(node: ast.expr) -> Number:
    """Recursively evaluate one arithmetic AST node."""
    match node:
        case ast.Constant(value=bool()):
            raise CalculationError("booleans are not numbers here")
        case ast.Constant(value=int() | float() as value):
            return value
        case ast.UnaryOp(op=op, operand=operand) if type(op) in UNARY_OPERATORS:
            return UNARY_OPERATORS[type(op)](evaluate_node(operand))
        case ast.BinOp(left=left, op=op, right=right) if type(op) in BINARY_OPERATORS:
            return apply_binary(op=op, left=evaluate_node(left), right=evaluate_node(right))
        case _:
            raise CalculationError(f"unsupported element: {ast.dump(node)}")


def apply_binary(*, op: ast.operator, left: Number, right: Number) -> Number:
    """Apply one binary operator, refusing huge powers and division by zero."""
    if isinstance(op, ast.Pow) and is_power_too_large(base=left, exponent=right):
        raise CalculationError("result is too large")
    try:
        return BINARY_OPERATORS[type(op)](left, right)
    except (ZeroDivisionError, OverflowError) as error:
        raise CalculationError(str(error)) from error


def is_power_too_large(*, base: Number, exponent: Number) -> bool:
    """Estimate the size of ``base ** exponent`` without computing it."""
    magnitude = abs(base)
    if magnitude <= 1:
        return False
    return int(magnitude).bit_length() * abs(exponent) > MAX_RESULT_BITS


@tool
def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression such as "(12.5 * 4) / 3" or "2 ** 10".

    Supports + - * / // % ** and parentheses. Use it for any arithmetic rather
    than computing in your head.
    """
    try:
        return str(evaluate_arithmetic(expression))
    except CalculationError as error:
        return f"Error: {error}"


@tool
def current_utc_time() -> str:
    """Return the current date and time in UTC, in ISO 8601 format."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def list_custom_tools() -> list[BaseTool]:
    """Tools this service adds on top of the deepagents defaults."""
    return [calculator, current_utc_time]
