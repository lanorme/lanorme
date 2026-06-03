"""SSE-001: streaming endpoints must handle client disconnect.

FastAPI / Starlette streaming endpoints that pass a local generator to
``StreamingResponse`` or ``EventSourceResponse`` can leak coroutines when
the client disconnects if the generator does not catch
``asyncio.CancelledError`` or poll ``request.is_disconnected()``.

Precision-first: only the unambiguous shape is flagged -- a locally-defined
generator called directly at the response-constructor call site.
Indirection (imported generators, dynamic construction) is left unflagged.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.checks._ast_helpers import attr_chain as _attr_chain
from lanorme.discovery import iter_py_files

_STREAMING_CTORS = frozenset({"StreamingResponse", "EventSourceResponse"})


def _is_streaming_ctor(call: ast.Call) -> bool:
    if isinstance(call.func, ast.Name):
        return call.func.id in _STREAMING_CTORS
    chain = _attr_chain(call.func)
    return bool(chain) and chain[-1] in _STREAMING_CTORS


def _ctor_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return _attr_chain(call.func)[-1]


def _content_call(call: ast.Call) -> ast.Call | None:
    """Return the first positional arg or 'content' kwarg if it is itself a call."""
    if call.args and isinstance(call.args[0], ast.Call):
        return call.args[0]
    for kw in call.keywords:
        if kw.arg == "content" and isinstance(kw.value, ast.Call):
            return kw.value
    return None


def _simple_name(node: ast.Call) -> str | None:
    return node.func.id if isinstance(node.func, ast.Name) else None


def _yield_check(node: ast.AST) -> bool:
    """True if *node* or its non-nested-function descendants contain a yield."""
    if isinstance(node, (ast.Yield, ast.YieldFrom)):
        return True
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    return any(_yield_check(child) for child in ast.iter_child_nodes(node))


def _is_generator(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(_yield_check(stmt) for stmt in func.body)


def _local_generators(
    tree: ast.AST,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    result: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_generator(node):
            result[node.name] = node
    return result


def _has_cancel_handler(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(func):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if handler.type is None:
                return True
            chain = _attr_chain(handler.type)
            if chain and chain[-1] == "CancelledError":
                return True
    return False


def _has_disconnect_check(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            chain = _attr_chain(node.func)
            if chain and chain[-1] == "is_disconnected":
                return True
    return False


def _scan_tree(*, tree: ast.AST, relative_file: str) -> list[Violation]:
    generators = _local_generators(tree)
    found: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_streaming_ctor(node):
            continue
        gen_call = _content_call(node)
        if gen_call is None:
            continue
        name = _simple_name(gen_call)
        if name is None or name not in generators:
            continue
        gen = generators[name]
        if _has_cancel_handler(gen) or _has_disconnect_check(gen):
            continue
        found.append(
            Violation(
                file=relative_file,
                line=gen.lineno,
                rule="SSE-001",
                message=(
                    f"Generator '{name}' passed to {_ctor_name(node)} "
                    "lacks client-disconnect handling"
                ),
                fix=(
                    "Wrap the yield loop in try/except asyncio.CancelledError, "
                    "or poll await request.is_disconnected() before each yield"
                ),
            )
        )
    return found


@dataclass
class StreamingSafetyCheck:
    """SSE-001: generator passed to StreamingResponse must handle client disconnect."""

    name: str = "streaming_safety"
    description: str = "Streaming endpoint disconnect-safety (SSE-001)"
    rules: list[str] = field(
        default_factory=lambda: [
            "SSE-001: Generator passed to StreamingResponse/EventSourceResponse "
            "must handle client disconnect (asyncio.CancelledError or is_disconnected())",
        ]
    )

    def run(self, *, src_root: str) -> CheckResult:
        violations: list[Violation] = []
        root = Path(src_root)
        for path in iter_py_files(root):
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            relative_file = str(path.relative_to(root))
            violations.extend(_scan_tree(tree=tree, relative_file=relative_file))
        status = Status.FAIL if violations else Status.PASS
        return CheckResult(check=self.name, status=status, violations=violations)


register(StreamingSafetyCheck())
