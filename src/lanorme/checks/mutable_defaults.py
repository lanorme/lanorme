"""MUTDEF-001: mutable default argument detection.

Functions with mutable default arguments (list, dict, set literals) share the
same object across every call that uses the default. This is one of the most
surprising footguns in Python: the default is evaluated once at function
definition time, not on each invocation.

    MUTDEF-001  mutable default argument (list, dict, or set literal)

The rule flags:
- ``ast.List`` literals as defaults  -- ``def f(x=[]):``
- ``ast.Dict`` literals as defaults  -- ``def f(x={}):``
- ``ast.Set`` literals as defaults   -- ``def f(x={1, 2}):``
- ``set()`` calls with no arguments  -- ``def f(x=set()):``

It does not flag ``None``, integers, strings, tuples, or any other immutable
default. Use ``None`` as the sentinel and construct the mutable inside the
function body.

Run:
    lanorme check . --check=mutable_defaults
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.discovery import iter_py_files

_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"})


def _is_mutable_default(node: ast.expr) -> bool:
    """Return True if *node* is a mutable default that should be flagged."""
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    # set() with no arguments is also mutable and a common alternative spelling.
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "set"
        and not node.args
        and not node.keywords
    ):
        return True
    return False


def _violations_for_funcdef(
    *,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    relative_file: str,
) -> list[Violation]:
    found: list[Violation] = []
    for default in node.args.defaults:
        if _is_mutable_default(default):
            found.append(
                Violation(
                    file=relative_file,
                    line=default.lineno,
                    rule="MUTDEF-001",
                    message=(
                        f"Mutable default argument in function '{node.name}': "
                        f"the object is shared across all calls that use the default"
                    ),
                    fix=(
                        "Use None as the default and construct the mutable inside the function body: "
                        "``if arg is None: arg = <mutable>``"
                    ),
                )
            )
    return found


@dataclass
class MutableDefaultsCheck:
    """Flag mutable default arguments (list, dict, set literals)."""

    name: str = "mutable_defaults"
    description: str = "Flag mutable default arguments (list, dict, set literals)"
    rules: list[str] = field(
        default_factory=lambda: [
            "MUTDEF-001: No list/dict/set literal (or set()) as a function default argument",
        ]
    )

    def _scan_tree(self, *, tree: ast.AST, relative_file: str) -> list[Violation]:
        found: list[Violation] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.extend(
                    _violations_for_funcdef(node=node, relative_file=relative_file)
                )
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


register(MutableDefaultsCheck())
