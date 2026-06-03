"""IMPORT-001, ENDPOINT-001, and TYPING-001: Pattern divergence detection.

Rules:
    IMPORT-001    No inline imports inside functions, all imports at module level
    ENDPOINT-001  Endpoint functions should not exceed nesting depth of 4 (warning)
    TYPING-001    Type-only imports should use TYPE_CHECKING guard (opt-in)

Run:
    lanorme check . --check=pattern_divergence
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.discovery import iter_py_files

# ---------------------------------------------------------------------------
# IMPORT-001: No inline imports inside functions
# ---------------------------------------------------------------------------

# ORM models commonly live under infrastructure/repositories/; TYPE_CHECKING
# guards are tolerated there for ORM forward references.
_MODELS_DIR = "infrastructure/repositories"

# Directories where conditional inline imports are legitimate:
# - observability: OTel instrumentation packages loaded conditionally
# - main.py: app factory wires dependencies at startup
_PATTERN_001_EXEMPT_PATHS = (
    "infrastructure/observability/",
    "api/v1/main.py",
)


def _is_inside_function(*, node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """Walk up the parent chain to see if *node* is inside a function body."""
    current_id = id(node)
    while current_id in parents:
        parent = parents[current_id]
        if isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef):
            return True
        current_id = id(parent)
    return False


def _is_inside_type_checking_guard(
    *,
    node: ast.AST,
    parents: dict[int, ast.AST],
) -> bool:
    """Return True if *node* lives inside an ``if TYPE_CHECKING:`` block."""
    current_id = id(node)
    while current_id in parents:
        parent = parents[current_id]
        if isinstance(parent, ast.If):
            test = parent.test
            # Plain ``TYPE_CHECKING``
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                return True
            # ``typing.TYPE_CHECKING``
            if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
                return True
        current_id = id(parent)
    return False


def _build_parent_map(*, tree: ast.AST) -> dict[int, ast.AST]:
    """Build a child-id → parent mapping for the entire AST."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _line_has_noqa(*, source_lines: list[str], lineno: int, rule: str) -> bool:
    """Return True if the source line has a ``# noqa: <rule>`` comment."""
    if 1 <= lineno <= len(source_lines):
        line = source_lines[lineno - 1]
        if f"# noqa: {rule}" in line:
            return True
    return False


def _check_inline_imports(
    *,
    tree: ast.AST,
    source_lines: list[str],
    relative_file: str,
) -> list[Violation]:
    """IMPORT-001: Find import statements inside function bodies."""
    # Exempt paths where conditional imports are legitimate.
    normalized = relative_file.replace("\\", "/")
    for exempt in _PATTERN_001_EXEMPT_PATHS:
        if normalized.startswith(exempt) or normalized == exempt:
            return []

    violations: list[Violation] = []
    parents = _build_parent_map(tree=tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue

        if not _is_inside_function(node=node, parents=parents):
            continue

        # Exempt imports inside TYPE_CHECKING guards (though discouraged).
        if _is_inside_type_checking_guard(node=node, parents=parents):
            continue

        if _line_has_noqa(
            source_lines=source_lines,
            lineno=node.lineno,
            rule="IMPORT-001",
        ):
            continue

        # Build a human-readable module name for the message.
        if isinstance(node, ast.ImportFrom):
            module_name = node.module or "<relative>"
        else:
            module_name = ", ".join(alias.name for alias in node.names)

        violations.append(
            Violation(
                file=relative_file,
                line=node.lineno,
                rule="IMPORT-001: No inline imports inside functions",
                message=f"Import '{module_name}' found inside a function body",
                fix="Move this import to the top of the file, at module level",
            ),
        )

    return violations


# ---------------------------------------------------------------------------
# ENDPOINT-001: Endpoint functions should not exceed complexity threshold
# ---------------------------------------------------------------------------

_ENDPOINTS_DIR = "api/v1/endpoints"
_MAX_NESTING_DEPTH = 4

# AST node types that introduce a new nesting level.
_NESTING_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.ExceptHandler,
)


def _max_nesting_depth(*, node: ast.AST, depth: int = 0) -> int:
    """Recursively compute the maximum nesting depth of control-flow nodes."""
    max_depth = depth

    for child in ast.iter_child_nodes(node):
        if isinstance(child, _NESTING_NODES):
            child_depth = _max_nesting_depth(node=child, depth=depth + 1)
        else:
            child_depth = _max_nesting_depth(node=child, depth=depth)
        max_depth = max(max_depth, child_depth)

    return max_depth


def _check_endpoint_nesting(
    *,
    tree: ast.AST,
    source_lines: list[str],
    relative_file: str,
) -> list[Violation]:
    """ENDPOINT-001: Flag endpoint functions with nesting > 4 levels."""
    normalized = relative_file.replace("\\", "/")
    if not normalized.startswith(_ENDPOINTS_DIR):
        return []

    warnings: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        depth = _max_nesting_depth(node=node)
        if depth <= _MAX_NESTING_DEPTH:
            continue

        if _line_has_noqa(
            source_lines=source_lines,
            lineno=node.lineno,
            rule="ENDPOINT-001",
        ):
            continue

        warnings.append(
            Violation(
                file=relative_file,
                line=node.lineno,
                rule="ENDPOINT-001: Endpoint nesting depth exceeds threshold (warning)",
                message=(
                    f"Function '{node.name}' has nesting depth {depth} "
                    f"(max {_MAX_NESTING_DEPTH}) — consider extracting helper functions"
                ),
                fix=(
                    "Extract deeply nested logic into private helper functions "
                    "or service methods to reduce cognitive complexity"
                ),
            ),
        )

    return warnings


# ---------------------------------------------------------------------------
# TYPING-001: Type-only imports should use TYPE_CHECKING guard (opt-in)
# ---------------------------------------------------------------------------

_TYPING_MODULES = frozenset({"typing", "typing_extensions"})


def _annotation_node_ids(*, tree: ast.AST) -> set[int]:
    """Return the set of node IDs for all AST nodes that are part of annotations.

    Covers:
    - ``AnnAssign.annotation``
    - ``arg.annotation`` (function parameter annotations)
    - ``FunctionDef.returns`` / ``AsyncFunctionDef.returns``
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        annotation: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            annotation = node.annotation
        elif isinstance(node, ast.arg):
            annotation = node.annotation
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            annotation = node.returns
        if annotation is not None:
            for child in ast.walk(annotation):
                ids.add(id(child))
    return ids


def _names_used_outside_annotations(*, tree: ast.AST, import_names: set[str]) -> set[str]:
    """Return the subset of *import_names* that appear in non-annotation contexts."""
    ann_ids = _annotation_node_ids(tree=tree)
    used_outside: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in ann_ids:
            continue
        if isinstance(node, ast.Name) and node.id in import_names:
            used_outside.add(node.id)
    return used_outside


def _check_typing_imports(
    *,
    tree: ast.AST,
    source_lines: list[str],
    relative_file: str,
    direction: str,
) -> list[Violation]:
    """TYPING-001: Flag typing imports that should (or should not) be guarded."""
    parents = _build_parent_map(tree=tree)
    violations: list[Violation] = []

    # Gather all imported names from typing / typing_extensions at module level.
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module not in _TYPING_MODULES:
            continue

        # Only consider module-level imports (not inside functions).
        if _is_inside_function(node=node, parents=parents):
            continue

        already_guarded = _is_inside_type_checking_guard(node=node, parents=parents)

        if _line_has_noqa(source_lines=source_lines, lineno=node.lineno, rule="TYPING-001"):
            continue

        import_names = {alias.asname or alias.name for alias in node.names}

        # Check whether any imported name is used at runtime (outside annotations).
        used_at_runtime = bool(
            _names_used_outside_annotations(tree=tree, import_names=import_names)
        )

        if direction == "encourage":
            # Flag unguarded imports whose names are only used in annotations.
            if not already_guarded and not used_at_runtime:
                violations.append(
                    Violation(
                        file=relative_file,
                        line=node.lineno,
                        rule="TYPING-001: Type-only imports should use TYPE_CHECKING guard",
                        message=(
                            f"Import '{node.module}' contains names used only in annotations; "
                            "wrap it in 'if TYPE_CHECKING:'"
                        ),
                        fix=(
                            "Add 'from __future__ import annotations' and wrap this import "
                            "in 'if TYPE_CHECKING:' to avoid the runtime cost"
                        ),
                    ),
                )
        elif direction == "forbid":
            # Flag imports that ARE guarded (project avoids TYPE_CHECKING guards).
            if already_guarded:
                violations.append(
                    Violation(
                        file=relative_file,
                        line=node.lineno,
                        rule="TYPING-001: Type-only imports should use TYPE_CHECKING guard",
                        message=(
                            f"Import '{node.module}' is inside a TYPE_CHECKING guard; "
                            "this project forbids TYPE_CHECKING guards — move it to module level"
                        ),
                        fix="Remove the 'if TYPE_CHECKING:' wrapper and import at module level",
                    ),
                )

    return violations


# ---------------------------------------------------------------------------
# Check class + registration
# ---------------------------------------------------------------------------


@dataclass
class PatternDivergenceCheck:
    """Detects pattern divergence: inline imports and over-nested endpoint functions."""

    name: str = "pattern_divergence"
    description: str = (
        "Pattern divergence detection (inline imports, TYPE_CHECKING, endpoint consistency)"
    )
    rules: list[str] = field(
        default_factory=lambda: [
            "IMPORT-001: No inline imports inside functions",
            "ENDPOINT-001: Endpoint nesting depth exceeds threshold (warning)",
            "TYPING-001: Type-only imports should use TYPE_CHECKING guard (opt-in)",
        ],
    )

    # TYPING-001 configuration (opt-in).
    typing_guard_enabled: bool = False
    typing_guard_direction: str = "encourage"

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.pattern_divergence]`` configuration."""
        enabled = settings.get("typing_guard_enabled")
        if isinstance(enabled, bool):
            self.typing_guard_enabled = enabled
        direction = settings.get("typing_guard_direction")
        if isinstance(direction, str) and direction in {"encourage", "forbid"}:
            self.typing_guard_direction = direction

    def run(self, *, src_root: str) -> CheckResult:
        """Scan Python files under src/ for pattern divergence."""
        violations: list[Violation] = []
        warnings: list[Violation] = []
        src_path = Path(src_root)

        for py_file in iter_py_files(src_path):
            relative_file = str(py_file.relative_to(src_path))

            # Skip test files.
            if Path(relative_file).name.startswith("test_"):
                continue

            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
            except (OSError, UnicodeDecodeError, SyntaxError):
                warnings.append(
                    Violation(
                        file=relative_file,
                        line=0,
                        rule="PATTERN-000: parse error",
                        message=f"Could not parse {py_file.name} — skipping",
                        fix="Fix the syntax error first",
                    ),
                )
                continue

            source_lines = source.splitlines()

            # IMPORT-001: inline imports (violation)
            violations.extend(
                _check_inline_imports(
                    tree=tree,
                    source_lines=source_lines,
                    relative_file=relative_file,
                ),
            )

            # ENDPOINT-001: endpoint nesting depth (warning)
            warnings.extend(
                _check_endpoint_nesting(
                    tree=tree,
                    source_lines=source_lines,
                    relative_file=relative_file,
                ),
            )

            # TYPING-001: type-only imports guard (opt-in violation)
            if self.typing_guard_enabled:
                violations.extend(
                    _check_typing_imports(
                        tree=tree,
                        source_lines=source_lines,
                        relative_file=relative_file,
                        direction=self.typing_guard_direction,
                    ),
                )

        status = Status.FAIL if violations else (Status.WARN if warnings else Status.PASS)
        return CheckResult(
            check=self.name,
            status=status,
            violations=violations,
            warnings=warnings,
        )


# Self-register on import.
register(PatternDivergenceCheck())
