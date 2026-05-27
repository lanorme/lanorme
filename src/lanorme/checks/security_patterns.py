"""AUTHN-001 through SECRETPY-001: Security pattern enforcement.

Checks:
    AUTHN-001  Mutation endpoints must have auth dependencies
    SQL-001  No raw SQL string literals, use an ORM or parameterized queries
    SECRETPY-001  No hardcoded secrets in source code

Run:
    lanorme check . --check=security_patterns
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register

# HTTP methods that mutate data, these MUST have auth.
MUTATION_METHODS = {"post", "put", "patch", "delete"}

# Auth dependency detection: any Depends() arg matching these prefixes counts as auth.
AUTH_DEPENDENCY_PREFIXES = ("get_current_user", "require_")

# Endpoints that are exempt from AUTHN-001, they ARE the auth boundary,
# so they cannot themselves require auth. Common auth-issuance and public
# discovery endpoints go here.
AUTH_EXEMPT_ENDPOINTS = {
    # Auth-issuance vocabulary.
    "login",
    "logout",
    "refresh",
    "token",
}

# SQL-001 detector vocabulary.
# A sink is a function/method call whose first argument is treated as SQL by a
# database driver. Method-form sinks (``.execute``, ``.executemany``, ...) only
# count when the receiver looks plausibly DB-shaped (i.e. NOT subprocess or an
# HTTP client). Function-form sinks (``text``, ``read_sql``, ``read_sql_query``)
# are unwrapped or treated as the sink depending on context.
_SQL_SINK_METHODS = frozenset({"execute", "executemany", "executescript"})
_SQL_READ_SINKS = frozenset({"read_sql", "read_sql_query"})

# Receiver names that mark an ``.execute`` call as non-DB and so out of scope.
_NON_DB_RECEIVER_HINTS = (
    "subprocess", "client", "http", "runner", "job", "task", "command",
    "shell", "process", "executor", "worker", "queue", "pool",
)

# A string looks like SQL when one of these keyword shapes appears.
_SQL_KEYWORDS_RE = re.compile(
    r"\b(SELECT\b.*?\bFROM\b|INSERT\s+INTO\b|UPDATE\b.*?\bSET\b|DELETE\s+FROM\b"
    r"|CREATE\s+(TABLE|INDEX|VIEW|SCHEMA)\b|DROP\s+(TABLE|INDEX|VIEW|SCHEMA)\b"
    r"|ALTER\s+TABLE\b|TRUNCATE\s+TABLE\b|MERGE\s+INTO\b|VACUUM\b|REINDEX\b)",
    re.IGNORECASE | re.DOTALL,
)

# Placeholder shapes a driver binds; SQL with a placeholder + a params arg is safe.
_SQL_PLACEHOLDER_RE = re.compile(r":[A-Za-z_]\w*|%s|%\([A-Za-z_]\w*\)s|\?")

# Patterns that indicate hardcoded secrets.
SECRET_PATTERNS = [
    re.compile(
        r'(?:password|passwd|secret|token|api_key|apikey)\s*=\s*["\'][^"\']{8,}["\']', re.IGNORECASE
    ),
    re.compile(
        r'(?:aws_access_key_id|aws_secret_access_key)\s*=\s*["\'][^"\']+["\']', re.IGNORECASE
    ),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}=*", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA )?PRIVATE KEY-----"),
]

# Files/paths to exclude from secret scanning (test fixtures, docs, etc.).
SECRET_SCAN_EXCLUDES = {
    "conftest.py",
    "seed_dev.py",
}


def _is_mutation_endpoint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Check if a function is a mutation endpoint. Return the HTTP method or None."""
    for decorator in node.decorator_list:
        # Match @router.post(...), @router.delete(...), etc.
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
            method = decorator.func.attr
            if method in MUTATION_METHODS:
                return method
    return None


def _is_auth_name(name: str) -> bool:
    """Check if a function name looks like an auth dependency."""
    return any(name.startswith(prefix) for prefix in AUTH_DEPENDENCY_PREFIXES)


def _has_auth_dependency(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function has an auth dependency in its parameters."""
    # Check both positional args and keyword-only args (after bare * separator).
    all_args = node.args.args + node.args.kwonlyargs
    for arg in all_args:
        if arg.annotation is None:
            continue
        # Walk the annotation AST looking for Depends(require_*) or Depends(get_current_user).
        for child in ast.walk(arg.annotation):
            if isinstance(child, ast.Call):
                for call_arg in child.args:
                    if isinstance(call_arg, ast.Name) and _is_auth_name(call_arg.id):
                        return True
                for kw in child.keywords:
                    if isinstance(kw.value, ast.Name) and _is_auth_name(kw.value.id):
                        return True
    return False


def _check_auth_on_mutations(
    *,
    tree: ast.AST,
    relative_file: str,
) -> list[Violation]:
    """AUTHN-001: Every mutation endpoint must have an auth dependency."""
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        method = _is_mutation_endpoint(node)
        if method is None:
            continue

        # Auth endpoints are exempt, they issue tokens, they can't require them.
        if node.name in AUTH_EXEMPT_ENDPOINTS:
            continue

        if not _has_auth_dependency(node):
            violations.append(
                Violation(
                    file=relative_file,
                    line=node.lineno,
                    rule="AUTHN-001: Mutation endpoints must have auth dependency",
                    message=f"@router.{method} endpoint '{node.name}' has no auth dependency",
                    fix=(
                        "Add a parameter like: "
                        "current_user: Annotated[AuthenticatedUser, Depends(get_current_user)]"
                    ),
                )
            )

    return violations


def _is_text_constructor(node: ast.expr) -> bool:
    """True if *node* is a ``text(...)`` / ``sa.text(...)`` SQL constructor call."""
    if not isinstance(node, ast.Call):
        return False
    return (
        (isinstance(node.func, ast.Name) and node.func.id == "text")
        or (isinstance(node.func, ast.Attribute) and node.func.attr == "text")
    )


def _literal_lineno(
    node: ast.expr, *, constants: dict[str, "_SqlConst"] | None = None
) -> int | None:
    """Return the source line of the SQL-bearing literal at *node*, or ``None``.

    Knows the same shapes as :func:`_sql_string_from`: literals, f-strings,
    BinOps, ``.format(...)`` calls, ``text(...)`` wrappers, and ``Name``
    references resolved via *constants*. For a ``Name`` whose binding lives
    elsewhere, we point at the assignment line in *constants*; otherwise we
    return ``None`` so the caller falls back to the call site.
    """
    if isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp):
        return node.lineno
    if isinstance(node, ast.Name) and constants is not None:
        entry = constants.get(node.id)
        return entry.lineno if entry is not None else None
    if isinstance(node, ast.Call):
        if _is_text_constructor(node) and node.args:
            return _literal_lineno(node.args[0], constants=constants)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            return node.lineno
    return None


def _sql_from_binop(
    node: ast.BinOp, *, constants: dict[str, "_SqlConst"] | None
) -> tuple[str | None, bool]:
    """Resolve ``"..." + x`` and ``"..." % x`` SQL-bearing BinOps."""
    if isinstance(node.op, ast.Add):
        left_text, _ = _sql_string_from(node.left, constants=constants)
        right_text, _ = _sql_string_from(node.right, constants=constants)
        if left_text is None and right_text is None:
            return None, False
        return (left_text or "") + (right_text or ""), True
    if isinstance(node.op, ast.Mod):
        left_text, _ = _sql_string_from(node.left, constants=constants)
        if left_text is not None:
            return left_text, True
    return None, False


def _sql_from_call(
    node: ast.Call, *, constants: dict[str, "_SqlConst"] | None
) -> tuple[str | None, bool]:
    """Resolve ``text(...)`` wrappers and ``"...".format(...)`` SQL-bearing calls."""
    if _is_text_constructor(node) and node.args:
        return _sql_string_from(node.args[0], constants=constants)
    if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        base_text, _ = _sql_string_from(node.func.value, constants=constants)
        if base_text is not None:
            return base_text, True
    return None, False


def _sql_string_from(
    node: ast.expr, *, constants: dict[str, "_SqlConst"] | None = None
) -> tuple[str | None, bool]:
    """Return ``(text, interpolated)`` for an SQL-argument AST node, or ``(None, False)``.

    Handles every shape that resolves to a SQL string before it reaches a sink:

    - ``"..."`` constant literal (``interpolated=False``).
    - ``f"... {x} ..."`` f-string (``interpolated=True`` when at least one
      ``FormattedValue`` is present).
    - ``"..." + name + "..."`` ``BinOp(Add)`` (``interpolated=True``).
    - ``"... %s ..." % name`` ``BinOp(Mod)`` (``interpolated=True``).
    - ``"...".format(name)`` (``interpolated=True``).
    - ``Name`` looked up in *constants* (preserving the constant's
      ``interpolated`` flag).
    - One-deep ``text(<expr>)`` / ``sa.text(<expr>)`` wrapper, unwrapped
      recursively.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.JoinedStr):
        text = "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else ""
            for v in node.values
        )
        interp = any(isinstance(v, ast.FormattedValue) for v in node.values)
        return text, interp
    if isinstance(node, ast.Name) and constants is not None:
        entry = constants.get(node.id)
        if entry is None:
            return None, False
        return entry.text, entry.interpolated
    if isinstance(node, ast.BinOp):
        return _sql_from_binop(node, constants=constants)
    if isinstance(node, ast.Call):
        return _sql_from_call(node, constants=constants)
    return None, False


@dataclass(frozen=True)
class _SqlConst:
    text: str
    interpolated: bool
    lineno: int


def _collect_string_constants(*, tree: ast.AST) -> dict[str, _SqlConst]:
    """Return ``{NAME: _SqlConst}`` for every ``NAME = "<str>"`` assign in *tree*.

    Walks the whole tree (not just module body), so function-local SQL
    variables like ``sql = f"..."`` are resolved when later passed to
    ``execute(text(sql))``. Later assignments overwrite earlier ones; that is
    acceptable since we only need *some* SQL string to flag the call.
    """
    constants: dict[str, _SqlConst] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        text, interp = _sql_string_from(node.value)
        if text is not None:
            constants[target.id] = _SqlConst(text=text, interpolated=interp, lineno=node.lineno)
    return constants


def _receiver_looks_non_db(call: ast.Call) -> bool:
    """True if ``call.func.value`` is named like an HTTP / shell / job runner."""
    if not isinstance(call.func, ast.Attribute):
        return False
    receiver = call.func.value
    name: str | None = None
    if isinstance(receiver, ast.Name):
        name = receiver.id.lower()
    elif isinstance(receiver, ast.Attribute):
        name = receiver.attr.lower()
    if name is None:
        return False
    return any(hint in name for hint in _NON_DB_RECEIVER_HINTS)


def _sink_kind(call: ast.Call) -> str | None:
    """Return ``"execute"`` / ``"read_sql"`` / ``None`` based on the call shape."""
    if isinstance(call.func, ast.Attribute):
        if call.func.attr in _SQL_SINK_METHODS:
            return None if _receiver_looks_non_db(call) else "execute"
        if call.func.attr in _SQL_READ_SINKS:
            return "read_sql"
    if isinstance(call.func, ast.Name) and call.func.id in _SQL_READ_SINKS:
        return "read_sql"
    return None


def _is_safely_parameterised(*, call: ast.Call, sql: str, kind: str) -> bool:
    """True if *sql* has a placeholder and the sink call also receives a params arg."""
    if not _SQL_PLACEHOLDER_RE.search(sql):
        return False
    for kw in call.keywords:
        if kw.arg in {"params", "parameters", "vars"}:
            return True
    # ``.execute(sql, params)``: second positional is the params bag.
    # ``read_sql(sql, con, params=...)``: second positional is the connection,
    # so only the explicit kwarg counts for read_sql.
    if kind == "execute" and len(call.args) >= 2:
        return True
    return False


def _check_raw_sql(
    *,
    tree: ast.AST,
    relative_file: str,
) -> list[Violation]:
    """SQL-001: only flag raw SQL that actually reaches a DB execution sink."""
    if "alembic" in relative_file or Path(relative_file).name.startswith("test_"):
        return []
    constants = _collect_string_constants(tree=tree)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = _sink_kind(node)
        if kind is None or not node.args:
            continue
        first = node.args[0]
        sql, interp = _sql_string_from(first, constants=constants)
        if sql is None or not _SQL_KEYWORDS_RE.search(sql):
            continue
        # Report at the literal's line where possible (the SQL text); for
        # Name references and text(Name) wrappers, point at the assignment
        # line in the constants map; otherwise fall back to the call site.
        report_lineno = _literal_lineno(first, constants=constants) or node.lineno
        if interp:
            violations.append(
                Violation(
                    file=relative_file,
                    line=report_lineno,
                    rule="SQL-001: No raw SQL — use an ORM or parameterized queries",
                    message="f-string interpolation into SQL is an injection vector",
                    fix="Bind the value as a parameter instead of interpolating it into the SQL text",
                )
            )
            continue
        if _is_safely_parameterised(call=node, sql=sql, kind=kind):
            continue
        snippet = " ".join(sql.split())[:80]
        violations.append(
            Violation(
                file=relative_file,
                line=report_lineno,
                rule="SQL-001: No raw SQL — use an ORM or parameterized queries",
                message=f"Raw SQL passed to a database sink: {snippet}",
                fix="Use an ORM expression, or bind values via parameters instead of inlining them",
            )
        )
    return violations


def _check_hardcoded_secrets(
    *,
    source: str,
    relative_file: str,
) -> list[Violation]:
    """SECRETPY-001: No hardcoded secrets in source code."""
    violations = []

    # Skip excluded files.
    file_name = Path(relative_file).name
    if any(file_name == exclude for exclude in SECRET_SCAN_EXCLUDES) or file_name.startswith(
        "test_"
    ):
        return violations

    for line_num, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()

        # Skip comments.
        if stripped.startswith("#"):
            continue

        for pattern in SECRET_PATTERNS:
            if pattern.search(line):
                # Exclude environment variable lookups and default placeholders.
                if "os.environ" in line or "os.getenv" in line or "settings." in line:
                    continue
                if '""' in line or "''" in line:
                    continue
                # Exclude type hints and docstrings.
                if "str =" not in line and "password" not in line.lower():
                    # Only flag if it really looks like an assignment.
                    pass

                violations.append(
                    Violation(
                        file=relative_file,
                        line=line_num,
                        rule="SECRETPY-001: No hardcoded secrets in source code",
                        message=f"Possible hardcoded secret: {stripped[:60]}...",
                        fix="Use environment variables or a secrets manager instead",
                    )
                )
                break

    return violations


@dataclass
class SecurityPatternsCheck:
    """Validates security patterns across the backend."""

    name: str = "security_patterns"
    description: str = "Security pattern enforcement (auth, SQL, secrets)"
    rules: list[str] = field(
        default_factory=lambda: [
            "AUTHN-001: Mutation endpoints must have auth dependency",
            "SQL-001: No raw SQL — use an ORM or parameterized queries",
            "SECRETPY-001: No hardcoded secrets in source code",
        ]
    )

    def run(self, *, src_root: str) -> CheckResult:
        """Scan all Python files under src/ for security violations."""
        violations: list[Violation] = []
        warnings: list[Violation] = []
        src_path = Path(src_root)

        for py_file in sorted(src_path.rglob("*.py")):
            relative_file = str(py_file.relative_to(src_path))

            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue

            # AUTHN-001: Only check endpoint files (api/ layer).
            if relative_file.startswith("api/"):
                violations.extend(_check_auth_on_mutations(tree=tree, relative_file=relative_file))

            # SQL-001: Check all files for raw SQL (except alembic).
            violations.extend(_check_raw_sql(tree=tree, relative_file=relative_file))

            # SECRETPY-001: Check all files for hardcoded secrets.
            violations.extend(_check_hardcoded_secrets(source=source, relative_file=relative_file))

        status = Status.FAIL if violations else (Status.WARN if warnings else Status.PASS)
        return CheckResult(
            check=self.name,
            status=status,
            violations=violations,
            warnings=warnings,
        )


# Self-register on import.
register(SecurityPatternsCheck())
