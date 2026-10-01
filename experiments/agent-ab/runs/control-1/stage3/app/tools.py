"""Custom tools given to the agent alongside the deepagents built-ins."""

import ast
import math
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
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round}
_NAMES = {"pi": math.pi, "e": math.e}
_MAX_EXPONENT = 1000


def _eval(node: ast.AST) -> float | int:
    match node:
        case ast.Expression(body=body):
            return _eval(body)
        case ast.Constant(value=value) if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        case ast.Name(id=name) if name in _NAMES:
            return _NAMES[name]
        case ast.BinOp(left=left, op=op, right=right) if type(op) in _BIN_OPS:
            lhs, rhs = _eval(left), _eval(right)
            if isinstance(op, ast.Pow) and abs(rhs) > _MAX_EXPONENT:
                raise ValueError("exponent too large")
            return _BIN_OPS[type(op)](lhs, rhs)
        case ast.UnaryOp(op=op, operand=operand) if type(op) in _UNARY_OPS:
            return _UNARY_OPS[type(op)](_eval(operand))
        case ast.Call(func=ast.Name(id=fname), args=args, keywords=[]) if fname in _FUNCS:
            return _FUNCS[fname](*(_eval(a) for a in args))
    raise ValueError(f"unsupported expression: {ast.dump(node)[:60]}")


@tool
def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression, e.g. "2 * (3 + 4) / sqrt(16)".

    Supports + - * / // % **, parentheses, pi, e, sqrt(), abs() and round().
    """
    try:
        result = _eval(ast.parse(expression, mode="eval"))
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        return f"Error: {exc}"
    return str(result)


@tool
def current_time(timezone_name: str = "UTC") -> str:
    """Return the current date and time in ISO 8601 format for an IANA timezone such as "Europe/London"."""
    try:
        tz = timezone.utc if timezone_name.upper() == "UTC" else ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return f"Error: unknown timezone {timezone_name!r}"
    return datetime.now(tz).isoformat(timespec="seconds")


CUSTOM_TOOLS = [calculator, current_time]
