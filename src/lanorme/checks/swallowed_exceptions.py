"""EXC-001: swallowed-exception detection.

Flags ``except`` handlers that absorb exceptions without re-raising,
logging, or printing them. Silent swallowing hides real failures and makes
bugs nearly impossible to diagnose in production.

    EXC-001     except handler swallows the exception without any signal

The rule fires only when **all** of the following hold:

1. The handler body contains no ``raise`` statement (re-raise or raise-from).
2. The handler body contains no call to a logging function (``log``,
   ``debug``, ``info``, ``warning``, ``error``, ``critical``, ``exception``
   as attribute calls, or ``print`` as a bare name call).
3. The handler body is not just a single string constant (a docstring/comment
   stand-in used in rare "expected to be empty" patterns).

Precision-first: the rule ignores ambiguous patterns rather than producing
false positives. Use ``# noqa: EXC-001`` to silence a legitimate suppression
(e.g. a top-level ``except Exception: pass`` that guards a graceful shutdown
path you have documented elsewhere).

Run:
    lanorme check . --check=swallowed_exceptions
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.discovery import iter_py_files

_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"})

_LOG_ATTRS = frozenset({"log", "debug", "info", "warning", "error", "critical", "exception"})


def _body_has_raise(body: list[ast.stmt]) -> bool:
    """Return True if any node in *body* (recursively) is a Raise statement."""
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Raise):
                return True
    return False


def _body_has_logging_call(body: list[ast.stmt]) -> bool:
    """Return True if *body* contains a logging or print call."""
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # logging.debug(...) / logger.warning(...) / log.error(...) etc.
            if isinstance(func, ast.Attribute) and func.attr in _LOG_ATTRS:
                return True
            # print(...)
            if isinstance(func, ast.Name) and func.id == "print":
                return True
    return False


def _body_is_docstring_only(body: list[ast.stmt]) -> bool:
    """Return True when the body is a single string-constant expression.

    Some codebases write ``except SomeError: "expected"`` as a readable
    no-op comment. Treat it the same as ``pass`` for the purposes of this
    rule (it still swallows silently), but the spec asks us not to flag it.
    """
    if len(body) != 1:
        return False
    stmt = body[0]
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _handler_violations(*, handler: ast.ExceptHandler, relative_file: str) -> list[Violation]:
    body = handler.body
    if _body_has_raise(body):
        return []
    if _body_has_logging_call(body):
        return []
    if _body_is_docstring_only(body):
        return []
    type_label = (
        ast.unparse(handler.type) if handler.type is not None else "Exception"
    )
    return [
        Violation(
            file=relative_file,
            line=handler.lineno,
            rule="EXC-001",
            message=f"except {type_label}: handler swallows the exception silently",
            fix=(
                "Add a raise, logging call, or print to surface the error; "
                "use # noqa: EXC-001 to acknowledge intentional suppression"
            ),
        )
    ]


@dataclass
class SwallowedExceptionsCheck:
    """Flag except handlers that absorb exceptions without any signal."""

    name: str = "swallowed_exceptions"
    description: str = "Flag except handlers that swallow exceptions silently"
    rules: list[str] = field(
        default_factory=lambda: [
            "EXC-001: No silent except handler (no raise, no logging, no print)",
        ]
    )

    def _scan_tree(self, *, tree: ast.AST, relative_file: str) -> list[Violation]:
        found: list[Violation] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                found.extend(_handler_violations(handler=node, relative_file=relative_file))
        return found

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
            violations.extend(self._scan_tree(tree=tree, relative_file=relative_file))
        status = Status.FAIL if violations else Status.PASS
        return CheckResult(check=self.name, status=status, violations=violations)


register(SwallowedExceptionsCheck())
