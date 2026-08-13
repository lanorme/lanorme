"""SIZE-001 through PARAM-001: File size, function length, complexity, and parameter enforcement.

Checks:
    SIZE-001  Python files warn and error on effective line count
    SIZE-002  Functions/methods warn and error on effective line count
    SIZE-003  Classes past the method limit are a warning (decomposition candidate)
    COMPLEXITY-001  Cyclomatic complexity warn and error per function
    PARAM-001  Parameter count warn and error (excluding self/cls)

Effective lines = non-blank, non-comment lines.

Every threshold defaults to the house standard below and is configurable per
project through ``[tool.lanorme.file_limits]``. Rule strings carry no number so
that a configured threshold does not change a finding's baseline anchor; the
number a finding was measured against lives in its message.

Excludes: __init__.py, conftest.py, alembic/, migrations/, test_* prefixed files.

Run:
    lanorme check . --check=file_limits
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.discovery import iter_py_files

# Default thresholds. Each is the default of the matching ``FileLimitsCheck``
# field, so ``[tool.lanorme.file_limits]`` overrides them per project.

# SIZE-001 thresholds (effective lines per file).
FILE_WARN_LINES = 300
FILE_ERROR_LINES = 500

# SIZE-002 thresholds (effective lines per function/method).
FUNC_WARN_LINES = 50
FUNC_ERROR_LINES = 80

# SIZE-003 threshold (methods per class).
CLASS_METHOD_WARN = 10

# COMPLEXITY-001 thresholds (cyclomatic complexity per function).
COMPLEXITY_WARN = 10
COMPLEXITY_ERROR = 15

# PARAM-001 thresholds (parameters per function, excluding self/cls).
PARAM_WARN = 5
PARAM_ERROR = 8

# Files and directories excluded from scanning.
EXCLUDED_FILENAMES = {"__init__.py", "conftest.py"}
EXCLUDED_DIR_PARTS = {"alembic", "migrations"}


@dataclass(frozen=True)
class _Bounds:
    """The warn/error pair one metric is measured against on this run.

    A warn threshold above its error threshold describes no reachable band, so
    the error value wins for both and everything at the limit reports an error.
    """

    warn: int
    error: int

    @classmethod
    def of(cls, *, warn: int, error: int) -> _Bounds:
        """Build a pair, collapsing the warn band when it sits above the error."""
        return cls(warn=min(warn, error), error=error)


def _should_exclude(*, file_path: Path) -> bool:
    """Determine whether a file should be skipped based on exclusion rules."""
    if file_path.name in EXCLUDED_FILENAMES:
        return True
    if file_path.name.startswith("test_"):
        return True
    return any(part in EXCLUDED_DIR_PARTS for part in file_path.parts)


def _count_effective_lines(*, source: str) -> int:
    """Count non-blank, non-comment-only lines in source code."""
    count = 0
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        count += 1
    return count


def _check_file_size(
    *,
    source: str,
    relative_file: str,
    bounds: _Bounds,
) -> tuple[list[Violation], list[Violation]]:
    """SIZE-001: Warn and error on a file's effective line count."""
    violations: list[Violation] = []
    warnings: list[Violation] = []
    effective = _count_effective_lines(source=source)

    if effective >= bounds.error:
        violations.append(
            Violation(
                file=relative_file,
                line=1,
                rule="SIZE-001: File exceeds the effective line limit",
                message=f"File has {effective} effective lines (limit: {bounds.error})",
                fix="Split into smaller modules with focused responsibilities",
            ),
        )
    elif effective >= bounds.warn:
        warnings.append(
            Violation(
                file=relative_file,
                line=1,
                rule="SIZE-001: File approaching the effective line limit",
                message=f"File has {effective} effective lines (warn: {bounds.warn})",
                fix="Consider splitting into smaller modules before it grows further",
            ),
        )

    return violations, warnings


def _check_function_lengths(
    *,
    tree: ast.AST,
    source: str,
    relative_file: str,
    bounds: _Bounds,
) -> tuple[list[Violation], list[Violation]]:
    """SIZE-002: Warn and error on a function's effective line count.

    Effective lines mirror SIZE-001: non-blank, non-comment-only lines within
    the function's span, so comments and blank lines never push a function
    over a threshold.
    """
    violations: list[Violation] = []
    warnings: list[Violation] = []
    source_lines = source.splitlines()

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        if node.end_lineno is None:
            continue

        span = "\n".join(source_lines[node.lineno - 1 : node.end_lineno])
        length = _count_effective_lines(source=span)

        if length >= bounds.error:
            violations.append(
                Violation(
                    file=relative_file,
                    line=node.lineno,
                    rule="SIZE-002: Function exceeds the line limit",
                    message=(
                        f"Function '{node.name}' is {length} effective lines "
                        f"(limit: {bounds.error})"
                    ),
                    fix="Extract helper functions or simplify control flow",
                ),
            )
        elif length >= bounds.warn:
            warnings.append(
                Violation(
                    file=relative_file,
                    line=node.lineno,
                    rule="SIZE-002: Function approaching the line limit",
                    message=(
                        f"Function '{node.name}' is {length} effective lines "
                        f"(warn: {bounds.warn})"
                    ),
                    fix="Consider extracting helper functions before it grows further",
                ),
            )

    return violations, warnings


def _check_class_method_count(
    *,
    tree: ast.AST,
    relative_file: str,
    limit: int,
) -> list[Violation]:
    """SIZE-003: Classes past the method limit are candidates for decomposition."""
    warnings: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        method_count = sum(
            1 for child in node.body if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
        )

        if method_count > limit:
            warnings.append(
                Violation(
                    file=relative_file,
                    line=node.lineno,
                    rule="SIZE-003: Class has too many methods",
                    message=f"Class '{node.name}' has {method_count} methods (warn: {limit})",
                    fix="Consider decomposing into smaller, focused classes",
                ),
            )

    return warnings


_BRANCHING_TYPES = (
    ast.If,
    ast.IfExp,
    ast.For,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.Assert,
)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _match_case_is_refutable(*, case: ast.match_case) -> bool:
    """True if a ``case`` arm can fail to match, so it is a real branch.

    An irrefutable arm always matches, like an ``else``: a bare capture or
    wildcard (``case x`` / ``case _``) with no guard. A guard makes any arm
    refutable, because the guard can be false (``case _ if cond``).
    """
    pattern = case.pattern
    irrefutable = (
        isinstance(pattern, ast.MatchAs)
        and pattern.pattern is None
        and case.guard is None
    )
    return not irrefutable


def _node_complexity_increment(*, node: ast.AST) -> int:
    """Extra paths one node introduces, not counting its descendants.

    Comprehensions count a branch per filter ``if`` and per nested ``for``
    clause; the primary ``for`` is the comprehension's iteration and is treated
    as a single expression (see RULES.md, COMPLEXITY-001). A ``match`` counts a
    branch per refutable ``case`` arm, mirroring ``if`` / ``elif``.
    """
    if isinstance(node, _BRANCHING_TYPES):
        return 1
    if isinstance(node, ast.BoolOp):
        return len(node.values) - 1
    if isinstance(node, ast.match_case):
        return 1 if _match_case_is_refutable(case=node) else 0
    if isinstance(node, _COMPREHENSIONS):
        return sum(len(gen.ifs) + int(index > 0) for index, gen in enumerate(node.generators))
    return 0


def _cyclomatic_complexity(*, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Calculate cyclomatic complexity for a single function.

    Walks only direct descendants, nested function bodies are excluded. Base
    complexity is 1, plus one per branching construct (``if`` / ``for`` /
    ``while`` / ``except`` / ``with`` / ``assert`` / ternary), one per extra
    boolean operand, one per refutable ``match`` case, and, for comprehensions,
    one per filter ``if`` and per nested ``for`` clause.
    """
    complexity = 1

    # Walk the function body but skip nested function definitions.
    nodes_to_visit: list[ast.AST] = list(func_node.body)
    while nodes_to_visit:
        node = nodes_to_visit.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        complexity += _node_complexity_increment(node=node)
        nodes_to_visit.extend(ast.iter_child_nodes(node))

    return complexity


@dataclass(frozen=True)
class _MetricSpec:
    """Messaging for one per-function numeric metric.

    Static by design: the thresholds arrive per run as ``_Bounds``, so a
    configured project reuses the same spec and the same rule strings.
    """

    metric: Callable[..., int]
    error_rule: str
    warn_rule: str
    noun: str
    fix_error: str
    fix_warn: str


def _check_function_metric(
    *,
    tree: ast.AST,
    relative_file: str,
    spec: _MetricSpec,
    bounds: _Bounds,
) -> tuple[list[Violation], list[Violation]]:
    """Apply a metric to every function and bucket the result into warn/error."""
    violations: list[Violation] = []
    warnings: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        value = spec.metric(func_node=node)

        if value >= bounds.error:
            violations.append(
                Violation(
                    file=relative_file,
                    line=node.lineno,
                    rule=spec.error_rule,
                    message=f"Function '{node.name}' has {spec.noun} {value} (limit: {bounds.error})",
                    fix=spec.fix_error,
                ),
            )
        elif value >= bounds.warn:
            warnings.append(
                Violation(
                    file=relative_file,
                    line=node.lineno,
                    rule=spec.warn_rule,
                    message=f"Function '{node.name}' has {spec.noun} {value} (warn: {bounds.warn})",
                    fix=spec.fix_warn,
                ),
            )

    return violations, warnings


def _count_parameters(*, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count function parameters, excluding self and cls."""
    params = func_node.args.posonlyargs + func_node.args.args + func_node.args.kwonlyargs
    count = len(params)

    # Exclude self/cls (first positional arg in methods).
    if params and params[0].arg in ("self", "cls"):
        count -= 1

    # Count *args and **kwargs if present.
    if func_node.args.vararg:
        count += 1
    if func_node.args.kwarg:
        count += 1

    return count


_COMPLEXITY_SPEC = _MetricSpec(
    metric=_cyclomatic_complexity,
    error_rule="COMPLEXITY-001: Function exceeds the complexity limit",
    warn_rule="COMPLEXITY-001: Function approaching the complexity limit",
    noun="cyclomatic complexity",
    fix_error="Extract helper functions or simplify branching logic",
    fix_warn="Consider simplifying branching logic before it grows further",
)

_PARAM_SPEC = _MetricSpec(
    metric=_count_parameters,
    error_rule="PARAM-001: Function exceeds the parameter limit",
    warn_rule="PARAM-001: Function approaching the parameter limit",
    noun="parameter count",
    fix_error="Group related parameters into a dataclass or TypedDict",
    fix_warn="Consider grouping related parameters into a dataclass or TypedDict",
)


_THRESHOLD_KEYS = (
    "file_warn_lines",
    "file_error_lines",
    "func_warn_lines",
    "func_error_lines",
    "class_method_warn",
    "complexity_warn",
    "complexity_error",
    "param_warn",
    "param_error",
)


@dataclass
class FileLimitsCheck:
    """Enforces file size and function length limits."""

    name: str = "file_limits"
    description: str = "File size, function length, complexity, and parameter enforcement"
    file_warn_lines: int = FILE_WARN_LINES
    file_error_lines: int = FILE_ERROR_LINES
    func_warn_lines: int = FUNC_WARN_LINES
    func_error_lines: int = FUNC_ERROR_LINES
    class_method_warn: int = CLASS_METHOD_WARN
    complexity_warn: int = COMPLEXITY_WARN
    complexity_error: int = COMPLEXITY_ERROR
    param_warn: int = PARAM_WARN
    param_error: int = PARAM_ERROR
    # The house defaults, stated as defaults. This listing is static metadata
    # for `lanorme rules`, never a finding's rule string, so it anchors nothing
    # and can afford to name the numbers a project starts from. Configured
    # values are read back from `lanorme check --show-config`.
    rules: list[str] = field(
        default_factory=lambda: [
            "SIZE-001: Python files warn at 300 effective lines by default, error at 500",
            "SIZE-002: Functions/methods warn at 50 effective lines by default, error at 80",
            "SIZE-003: Classes past 10 methods are a warning by default",
            "COMPLEXITY-001: Cyclomatic complexity warn at 10 by default, error at 15",
            "PARAM-001: Parameter count warn at 5 by default, error at 8 (excluding self/cls)",
        ],
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.file_limits]`` configuration.

        Every key is optional; an unset key keeps the house default. The check
        is default-on, so there is no ``enabled`` key: these tune the limits
        rather than switch the rules off.
        """
        for key in _THRESHOLD_KEYS:
            if key in settings:
                setattr(self, key, int(settings[key]))  # type: ignore[call-overload]

    def _bounds(self) -> tuple[_Bounds, _Bounds, _Bounds, _Bounds]:
        """The file, function, complexity and parameter pairs for this run."""
        return (
            _Bounds.of(warn=self.file_warn_lines, error=self.file_error_lines),
            _Bounds.of(warn=self.func_warn_lines, error=self.func_error_lines),
            _Bounds.of(warn=self.complexity_warn, error=self.complexity_error),
            _Bounds.of(warn=self.param_warn, error=self.param_error),
        )

    def _scan_source(
        self,
        *,
        tree: ast.AST,
        source: str,
        relative_file: str,
    ) -> tuple[list[Violation], list[Violation]]:
        """Every rule in this check, applied to one parsed file."""
        violations: list[Violation] = []
        warnings: list[Violation] = []
        file_bounds, func_bounds, complexity_bounds, param_bounds = self._bounds()

        # SIZE-001: File effective line count.
        found, warned = _check_file_size(
            source=source,
            relative_file=relative_file,
            bounds=file_bounds,
        )
        violations.extend(found)
        warnings.extend(warned)

        # SIZE-002: Function/method effective length.
        found, warned = _check_function_lengths(
            tree=tree,
            source=source,
            relative_file=relative_file,
            bounds=func_bounds,
        )
        violations.extend(found)
        warnings.extend(warned)

        # SIZE-003: Class method count.
        warnings.extend(
            _check_class_method_count(
                tree=tree,
                relative_file=relative_file,
                limit=self.class_method_warn,
            ),
        )

        # COMPLEXITY-001 and PARAM-001: per-function numeric metrics.
        for spec, bounds in ((_COMPLEXITY_SPEC, complexity_bounds), (_PARAM_SPEC, param_bounds)):
            found, warned = _check_function_metric(
                tree=tree,
                relative_file=relative_file,
                spec=spec,
                bounds=bounds,
            )
            violations.extend(found)
            warnings.extend(warned)

        return violations, warnings

    def run(self, *, src_root: str) -> CheckResult:
        """Scan all Python files under src/ and enforce size limits."""
        violations: list[Violation] = []
        warnings: list[Violation] = []
        src_path = Path(src_root)

        for py_file in iter_py_files(src_path):
            if _should_exclude(file_path=py_file):
                continue

            relative_file = py_file.relative_to(src_path).as_posix()

            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
            except (OSError, UnicodeDecodeError, SyntaxError):
                warnings.append(
                    Violation(
                        file=relative_file,
                        line=0,
                        rule="SIZE-000: parse error",
                        message=f"Could not parse {py_file.name} — skipping",
                        fix="Fix the syntax error first",
                    ),
                )
                continue

            found, warned = self._scan_source(
                tree=tree,
                source=source,
                relative_file=relative_file,
            )
            violations.extend(found)
            warnings.extend(warned)

        status = Status.FAIL if violations else (Status.WARN if warnings else Status.PASS)
        return CheckResult(
            check=self.name,
            status=status,
            violations=violations,
            warnings=warnings,
        )


# Self-register on import.
register(FileLimitsCheck())
