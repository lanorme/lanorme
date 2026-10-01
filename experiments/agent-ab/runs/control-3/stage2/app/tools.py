"""Custom tools given to the agent in addition to the deepagents built-ins."""

import ast
import operator
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import tool

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_EXPONENT = 1000


def _eval_node(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ValueError("exponent too large")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"unsupported expression element: {type(node).__name__}")


def evaluate(expression: str) -> float | int:
    """Safely evaluate an arithmetic expression (no names, calls or attributes)."""
    if len(expression) > 200:
        raise ValueError("expression too long")
    return _eval_node(ast.parse(expression, mode="eval"))


@tool
def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression such as "(2 + 3) * 4 / 5" or "2 ** 10".

    Supports + - * / // % ** and parentheses.
    """
    try:
        return str(evaluate(expression))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as exc:
        return f"Error: {exc}"


@tool
def current_time(timezone_name: str = "UTC") -> str:
    """Return the current date and time in ISO 8601 format for an IANA timezone
    such as "UTC" or "Europe/London"."""
    try:
        tz = timezone.utc if timezone_name.upper() == "UTC" else ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return f"Error: unknown timezone {timezone_name!r}"
    return datetime.now(tz).isoformat(timespec="seconds")


CUSTOM_TOOLS = [calculator, current_time]
