"""SSE-001: streaming endpoints missing client disconnect handling.

Async functions that return a ``StreamingResponse`` or
``EventSourceResponse`` should handle client disconnects, otherwise a slow
or infinite generator will keep running after the client has gone away,
leaking resources and producing needless back-pressure.

Two patterns are accepted as sufficient:

1. A ``try/except`` block that catches ``asyncio.CancelledError`` (or any
   bare ``CancelledError`` name) somewhere in the function body.
2. A call to ``request.is_disconnected()`` (or any ``is_disconnected()``
   call) somewhere in the function body.

The check is advisory (WARNING) and opt-in (default-off). Enable via::

    [tool.lanorme.streaming_disconnect]
    enabled = true

Rule codes are stable and part of the public surface:

    SSE-001  Streaming endpoint lacks client-disconnect handling.

Run:
    lanorme check . --check=streaming_disconnect
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.discovery import iter_py_files

_STREAMING_RESPONSE_NAMES = frozenset({"StreamingResponse", "EventSourceResponse"})


def _returns_streaming(func: ast.AsyncFunctionDef) -> bool:
    """Return True if the function body contains a ``return StreamingResponse(...)``
    or ``return EventSourceResponse(...)`` call."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Return):
            continue
        value = node.value
        if value is None:
            continue
        # Direct call: return StreamingResponse(...)
        if isinstance(value, ast.Call):
            func_node = value.func
            if isinstance(func_node, ast.Name) and func_node.id in _STREAMING_RESPONSE_NAMES:
                return True
            # Attribute call: return some.StreamingResponse(...)
            if (
                isinstance(func_node, ast.Attribute)
                and func_node.attr in _STREAMING_RESPONSE_NAMES
            ):
                return True
    return False


def _has_cancelled_error_handler(func: ast.AsyncFunctionDef) -> bool:
    """Return True if any ``try/except`` in the function catches ``CancelledError``."""
    for node in ast.walk(func):
        if not isinstance(node, ast.ExceptHandler):
            continue
        exc_type = node.type
        if exc_type is None:
            # bare ``except:`` -- catches everything, including CancelledError
            return True
        # except CancelledError or except asyncio.CancelledError
        if isinstance(exc_type, ast.Name) and exc_type.id == "CancelledError":
            return True
        if isinstance(exc_type, ast.Attribute) and exc_type.attr == "CancelledError":
            return True
        # except (CancelledError, ...) tuple
        if isinstance(exc_type, ast.Tuple):
            for elt in exc_type.elts:
                if isinstance(elt, ast.Name) and elt.id == "CancelledError":
                    return True
                if isinstance(elt, ast.Attribute) and elt.attr == "CancelledError":
                    return True
    return False


def _has_is_disconnected_call(func: ast.AsyncFunctionDef) -> bool:
    """Return True if the function body contains any ``is_disconnected()`` call."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        func_node = node.func
        # Direct bare call: is_disconnected()
        if isinstance(func_node, ast.Name) and func_node.id == "is_disconnected":
            return True
        # Attribute call: request.is_disconnected()
        if isinstance(func_node, ast.Attribute) and func_node.attr == "is_disconnected":
            return True
    return False


def _sse001(*, relative: str, line: int) -> Violation:
    return Violation(
        file=relative,
        line=line,
        rule="SSE-001: Streaming endpoint lacks client-disconnect handling",
        message=(
            "Async streaming endpoint returns StreamingResponse or EventSourceResponse "
            "without handling client disconnects"
        ),
        fix=(
            "Wrap the generator body in try/except CancelledError, "
            "or poll await request.is_disconnected() inside the generator"
        ),
    )


@dataclass
class StreamingDisconnectCheck:
    """Flags streaming endpoints missing client disconnect handling."""

    name: str = "streaming_disconnect"
    description: str = "Flag streaming endpoints missing client disconnect handling"
    enabled: bool = False
    rules: list[str] = field(
        default_factory=lambda: [
            "SSE-001: Streaming endpoint lacks client-disconnect handling",
        ]
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.streaming_disconnect]`` configuration."""
        self.enabled = bool(settings.get("enabled", False))

    def run(self, *, src_root: str) -> CheckResult:
        if not self.enabled:
            return CheckResult(check=self.name, status=Status.PASS, violations=[], warnings=[])

        warnings: list[Violation] = []
        root = Path(src_root)
        for path in iter_py_files(root):
            relative = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue
                if not _returns_streaming(node):
                    continue
                if _has_cancelled_error_handler(node):
                    continue
                if _has_is_disconnected_call(node):
                    continue
                warnings.append(_sse001(relative=relative, line=node.lineno))

        status = Status.WARN if warnings else Status.PASS
        return CheckResult(check=self.name, status=status, violations=[], warnings=warnings)


# Self-register on import.
register(StreamingDisconnectCheck())
