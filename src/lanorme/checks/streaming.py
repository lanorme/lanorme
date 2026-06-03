"""SSE-001: streaming endpoints must handle client disconnect.

Async generator functions used in ``StreamingResponse`` or
``EventSourceResponse`` that neither catch ``asyncio.CancelledError`` nor poll
``await request.is_disconnected()`` will leak the generator coroutine on every
client disconnect.

    SSE-001  async generator in a streaming response lacks disconnect handling

Precision-first: only fires when an async generator function is directly
referenced in a ``StreamingResponse(gen())`` or ``EventSourceResponse(gen())``
call in the same file and that function lacks both forms of disconnect handling.
False negatives are preferred over false positives.

Run:
    lanorme check . --check=streaming
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.discovery import iter_py_files

_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"})

_STREAMING_CLASSES = frozenset({"StreamingResponse", "EventSourceResponse"})

_CANCELLED_NAMES = frozenset({"CancelledError"})
_ASYNCIO_CANCELLED: tuple[str, str] = ("asyncio", "CancelledError")
_CATCHES_ALL = frozenset({"BaseException"})


def _attr_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return tuple(reversed(parts))
    return ()


def _direct_has_yield(func: ast.AsyncFunctionDef) -> bool:
    """True if *func* directly yields, ignoring nested function definitions."""
    stack: list[ast.AST] = list(func.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return False


def _exc_catches_cancelled(exc_type: ast.expr | None) -> bool:
    if exc_type is None:
        return True  # bare except
    if isinstance(exc_type, ast.Name):
        return exc_type.id in _CANCELLED_NAMES or exc_type.id in _CATCHES_ALL
    if isinstance(exc_type, ast.Attribute):
        return _attr_chain(exc_type) == _ASYNCIO_CANCELLED
    if isinstance(exc_type, ast.Tuple):
        return any(_exc_catches_cancelled(elt) for elt in exc_type.elts)
    return False


def _has_cancelled_handler(func: ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.ExceptHandler) and _exc_catches_cancelled(node.type):
            return True
    return False


def _has_disconnected_check(func: ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            chain = _attr_chain(node.value.func)
            if chain and chain[-1] == "is_disconnected":
                return True
    return False


def _streaming_gen_name(call: ast.Call) -> str | None:
    """Return the generator function name from a streaming response call, or None."""
    if isinstance(call.func, ast.Name):
        if call.func.id not in _STREAMING_CLASSES:
            return None
    elif isinstance(call.func, ast.Attribute):
        if call.func.attr not in _STREAMING_CLASSES:
            return None
    else:
        return None

    content: ast.expr | None = None
    if call.args:
        content = call.args[0]
    else:
        for kw in call.keywords:
            if kw.arg == "content":
                content = kw.value
                break

    if isinstance(content, ast.Call) and isinstance(content.func, ast.Name):
        return content.func.id
    return None


def _scan_tree(*, tree: ast.AST, relative_file: str) -> list[Violation]:
    async_gens: dict[str, ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and _direct_has_yield(node):
            async_gens[node.name] = node

    if not async_gens:
        return []

    violations: list[Violation] = []
    flagged: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        gen_name = _streaming_gen_name(node)
        if gen_name is None or gen_name not in async_gens or gen_name in flagged:
            continue
        func = async_gens[gen_name]
        if _has_cancelled_handler(func) or _has_disconnected_check(func):
            continue
        flagged.add(gen_name)
        violations.append(
            Violation(
                file=relative_file,
                line=func.lineno,
                rule="SSE-001",
                message=(
                    f"async generator '{gen_name}' used in a streaming response"
                    " does not handle client disconnect"
                ),
                fix=(
                    "Wrap the generator body in try/except asyncio.CancelledError,"
                    " or poll 'await request.is_disconnected()' and break on True"
                ),
            )
        )
    return violations


@dataclass
class StreamingCheck:
    """SSE-001: async generators in streaming responses must handle client disconnect."""

    name: str = "streaming"
    description: str = "Streaming endpoints must handle client disconnect (SSE-001)"
    rules: list[str] = field(
        default_factory=lambda: [
            "SSE-001: async generator in a streaming response lacks disconnect handling",
        ]
    )

    def run(self, *, src_root: str) -> CheckResult:
        violations: list[Violation] = []
        root = Path(src_root)
        for path in iter_py_files(root):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            relative_file = str(path.relative_to(root))
            violations.extend(_scan_tree(tree=tree, relative_file=relative_file))
        status = Status.FAIL if violations else Status.PASS
        return CheckResult(check=self.name, status=status, violations=violations)


register(StreamingCheck())
