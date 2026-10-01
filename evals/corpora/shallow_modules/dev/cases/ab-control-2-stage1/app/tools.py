"""Tools given to the agent in addition to the deepagents built-ins."""

from __future__ import annotations

import ast
import math
import operator
from datetime import datetime
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
_FUNCTIONS = {"sqrt": math.sqrt, "abs": abs, "round": round, "log": math.log, "exp": math.exp}
_CONSTANTS = {"pi": math.pi, "e": math.e}
_MAX_EXPONENT = 1000


def evaluate(expression: str) -> float | int:
    """Safely evaluate an arithmetic expression (no names or calls beyond a small whitelist)."""

    def ev(node: ast.AST) -> float | int:
        match node:
            case ast.Expression(body=body):
                return ev(body)
            case ast.Constant(value=value) if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
            case ast.BinOp(left=left, op=op, right=right) if type(op) in _BIN_OPS:
                lhs, rhs = ev(left), ev(right)
                if isinstance(op, ast.Pow) and abs(rhs) > _MAX_EXPONENT:
                    raise ValueError("exponent too large")
                return _BIN_OPS[type(op)](lhs, rhs)
            case ast.UnaryOp(op=op, operand=operand) if type(op) in _UNARY_OPS:
                return _UNARY_OPS[type(op)](ev(operand))
            case ast.Name(id=name) if name in _CONSTANTS:
                return _CONSTANTS[name]
            case ast.Call(func=ast.Name(id=name), args=args, keywords=[]) if name in _FUNCTIONS:
                return _FUNCTIONS[name](*(ev(a) for a in args))
        raise ValueError(f"unsupported expression: {ast.unparse(node)}")

    return ev(ast.parse(expression, mode="eval"))


@tool
def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression, e.g. "(2 + 3) * 4" or "sqrt(16) / 2".

    Supports + - * / // % **, parentheses, pi, e, and sqrt/abs/round/log/exp.
    """
    try:
        return str(evaluate(expression))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError) as exc:
        return f"Error: {exc}"


@tool
def current_time(timezone: str = "UTC") -> str:
    """Return the current date and time in ISO 8601 format for an IANA timezone such as "Europe/London"."""
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return f"Error: unknown timezone {timezone!r}"
    return datetime.now(tz).isoformat(timespec="seconds")


TOOLS = [calculator, current_time]
