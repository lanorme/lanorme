"""AUTHN-001 and SQL-001: web-shaped security checks.

Checks:
    AUTHN-001  Mutation endpoints must have an auth dependency
    SQL-001    No raw SQL string literals; use an ORM or parameterised queries

SECRETPY-001 (hardcoded secrets) lives in ``secrets.py`` as a sibling check.

AUTHN-001 scans the ``api/`` layer only. When the package sits under a nested
directory (a src layout), set the top-level ``[tool.lanorme] source_root``
(e.g. ``"src/myapp"``) so ``api/`` is located relative to it; without that the
layer is never found and no endpoint is inspected.

Run:
    lanorme check . --check=security_patterns
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.astnames import read_str_constant
from lanorme.checkconfig import read_str
from lanorme.sources import TOO_DEEP, Module, iter_parsed_modules, build_skip_notice, locate

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
    "subprocess",
    "client",
    "http",
    "runner",
    "job",
    "task",
    "command",
    "shell",
    "process",
    "executor",
    "worker",
    "queue",
    "pool",
)

# A string looks like SQL when one of these keyword shapes appears.
_SQL_KEYWORDS_RE = re.compile(
    r"\b(SELECT\b.*?\bFROM\b|INSERT\s+INTO\b|UPDATE\b.*?\bSET\b|DELETE\s+FROM\b"
    r"|CREATE\s+(TABLE|INDEX|VIEW|SCHEMA)\b|DROP\s+(TABLE|INDEX|VIEW|SCHEMA)\b"
    r"|ALTER\s+TABLE\b|TRUNCATE\s+TABLE\b|MERGE\s+INTO\b|VACUUM\b|REINDEX\b)",
    re.IGNORECASE | re.DOTALL,
)

# The module's ``NAME = "<sql>"`` bindings, when a sink is handed a bare Name.
_SqlConstants = dict[str, "_SqlConst"] | None

# Placeholder shapes a driver binds; SQL with a placeholder + a params arg is safe.
_SQL_PLACEHOLDER_RE = re.compile(r":[A-Za-z_]\w*|%s|%\([A-Za-z_]\w*\)s|\?")


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


def _collect_dependency_sites(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.expr]:
    """Where FastAPI accepts a ``Depends(...)``: annotations, defaults, ``dependencies=``."""
    arguments = node.args
    params = (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    sites: list[ast.expr] = [arg.annotation for arg in params if arg.annotation is not None]
    sites.extend(d for d in (*arguments.defaults, *arguments.kw_defaults) if d is not None)
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call):
            sites.extend(kw.value for kw in decorator.keywords if kw.arg == "dependencies")
    return sites


def _is_auth_call(call: ast.Call) -> bool:
    """True for ``Depends(require_*)`` / ``Security(get_current_user, ...)`` shapes."""
    operands = (*call.args, *(kw.value for kw in call.keywords))
    return any(isinstance(o, ast.Name) and _is_auth_name(o.id) for o in operands)


def _has_auth_dependency(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function declares an auth dependency anywhere FastAPI accepts one."""
    for site in _collect_dependency_sites(node):
        for child in ast.walk(site):
            if isinstance(child, ast.Call) and _is_auth_call(child):
                return True
    return False


def _check_auth_on_mutations(*, module: Module) -> list[Violation]:
    """AUTHN-001: Every mutation endpoint must have an auth dependency."""
    violations = []

    for node in module.index.functions:
        method = _is_mutation_endpoint(node)
        if method is None:
            continue

        # Auth endpoints are exempt, they issue tokens, they can't require them.
        if node.name in AUTH_EXEMPT_ENDPOINTS:
            continue

        if not _has_auth_dependency(node):
            violations.append(
                Violation(
                    file=module.relative,
                    line=node.lineno,
                    rule="AUTHN-001: Mutation endpoints must have auth dependency",
                    message=f"@router.{method} endpoint '{node.name}' has no auth dependency",
                    fix=(
                        "Add a parameter like: "
                        "current_user: Annotated[AuthenticatedUser, Depends(get_current_user)]"
                    ),
                    **locate(node),
                ),
            )

    return violations


def _is_text_constructor(node: ast.expr) -> bool:
    """True if *node* is a ``text(...)`` / ``sa.text(...)`` SQL constructor call."""
    if not isinstance(node, ast.Call):
        return False
    return (isinstance(node.func, ast.Name) and node.func.id == "text") or (
        isinstance(node.func, ast.Attribute) and node.func.attr == "text"
    )


def _find_literal_node(node: ast.expr) -> ast.expr | None:
    """Return the SQL-bearing literal at *node* (``text(...)`` unwrapped), or ``None``.

    Knows the literal shapes of :func:`_extract_sql_string`: constants, f-strings,
    BinOps and ``.format(...)`` calls. A ``Name`` has no literal of its own, so
    it resolves to ``None`` and the caller looks it up in its constants.
    """
    if isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp):
        return node
    if isinstance(node, ast.Call):
        if _is_text_constructor(node) and node.args:
            return _find_literal_node(node.args[0])
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            return node
    return None


def _find_literal_lineno(node: ast.expr, *, constants: _SqlConstants = None) -> int | None:
    """Return the source line of the SQL-bearing literal at *node*, or ``None``.

    Knows the same shapes as :func:`_extract_sql_string`: literals, f-strings,
    BinOps, ``.format(...)`` calls, ``text(...)`` wrappers, and ``Name``
    references resolved via *constants*. For a ``Name`` whose binding lives
    elsewhere, we point at the assignment line in *constants*; otherwise we
    return ``None`` so the caller falls back to the call site.
    """
    literal = _find_literal_node(node)
    if literal is not None:
        return literal.lineno
    while isinstance(node, ast.Call) and _is_text_constructor(node) and node.args:
        node = node.args[0]
    if isinstance(node, ast.Name) and constants is not None:
        entry = constants.get(node.id)
        return entry.lineno if entry is not None else None
    return None


def _sql_from_concat(node: ast.BinOp, *, constants: _SqlConstants) -> tuple[str | None, bool]:
    """Resolve ``"..." + x``: static only when every piece is a literal or literal constant."""
    left_text, left_interp = _extract_sql_string(node.left, constants=constants)
    right_text, right_interp = _extract_sql_string(node.right, constants=constants)
    if left_text is None and right_text is None:
        return None, False
    interpolated = left_interp or right_interp or None in (left_text, right_text)
    return (left_text or "") + (right_text or ""), interpolated


def _sql_from_binop(node: ast.BinOp, *, constants: _SqlConstants) -> tuple[str | None, bool]:
    """Resolve ``"..." + x`` and ``"..." % x`` SQL-bearing BinOps."""
    if isinstance(node.op, ast.Add):
        return _sql_from_concat(node, constants=constants)
    if isinstance(node.op, ast.Mod):
        left_text, _ = _extract_sql_string(node.left, constants=constants)
        if left_text is not None:
            return left_text, True
    return None, False


def _sql_from_call(node: ast.Call, *, constants: _SqlConstants) -> tuple[str | None, bool]:
    """Resolve ``text(...)`` wrappers and ``"...".format(...)`` SQL-bearing calls."""
    if _is_text_constructor(node) and node.args:
        return _extract_sql_string(node.args[0], constants=constants)
    if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        base_text, _ = _extract_sql_string(node.func.value, constants=constants)
        if base_text is not None:
            return base_text, True
    return None, False


def _extract_sql_string(
    node: ast.expr,
    *,
    constants: _SqlConstants = None,
) -> tuple[str | None, bool]:
    """Return ``(text, interpolated)`` for an SQL-argument AST node, or ``(None, False)``.

    Handles every shape that resolves to a SQL string before it reaches a sink:

    - ``"..."`` constant literal (``interpolated=False``).
    - ``f"... {x} ..."`` f-string (``interpolated=True`` when at least one
      ``FormattedValue`` is present).
    - ``"..." + name + "..."`` ``BinOp(Add)`` (``interpolated`` unless every piece is static).
    - ``"... %s ..." % name`` ``BinOp(Mod)`` (``interpolated=True``).
    - ``"...".format(name)`` (``interpolated=True``).
    - ``Name`` looked up in *constants* (preserving the constant's
      ``interpolated`` flag).
    - One-deep ``text(<expr>)`` / ``sa.text(<expr>)`` wrapper, unwrapped
      recursively.
    """
    match node:
        case ast.Constant(value=str() as text):
            return text, False
        case ast.JoinedStr(values=parts):
            text = "".join(read_str_constant(part) or "" for part in parts)
            return text, any(isinstance(part, ast.FormattedValue) for part in parts)
        case ast.Name(id=name) if constants is not None:
            entry = constants.get(name)
            if entry is None:
                return None, False
            return entry.text, entry.interpolated
        case ast.BinOp():
            return _sql_from_binop(node, constants=constants)
        case ast.Call():
            return _sql_from_call(node, constants=constants)
    return None, False


@dataclass(frozen=True)
class _SqlConst:
    text: str
    interpolated: bool
    lineno: int


def _collect_string_constants(*, module: Module) -> dict[str, _SqlConst]:
    """Return ``{NAME: _SqlConst}`` for every ``NAME = "<str>"`` assign in *tree*.

    Walks the whole tree (not just module body), so function-local SQL
    variables like ``sql = f"..."`` are resolved when later passed to
    ``execute(text(sql))``. Later assignments overwrite earlier ones; that is
    acceptable since we only need *some* SQL string to flag the call.
    """
    constants: dict[str, _SqlConst] = {}
    for node in module.index.collect(ast.Assign):
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        text, interp = _extract_sql_string(node.value)
        if text is not None:
            constants[target.id] = _SqlConst(text=text, interpolated=interp, lineno=node.lineno)
    return constants


def _is_non_db_receiver(call: ast.Call) -> bool:
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


def _classify_sink(call: ast.Call) -> str | None:
    """Return ``"execute"`` / ``"read_sql"`` / ``None`` based on the call shape."""
    if isinstance(call.func, ast.Attribute):
        if call.func.attr in _SQL_SINK_METHODS:
            return None if _is_non_db_receiver(call) else "execute"
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


def _build_finding_span(
    *,
    first: ast.expr,
    call: ast.Call,
    report_lineno: int,
) -> dict[str, int | None]:
    """The span of an SQL-001 finding reported at *report_lineno*.

    The literal when it sits inside the call, the call itself when the line
    fell back to it, and nothing when the line points at an assignment
    elsewhere in the file (no node for it is at hand).
    """
    literal = _find_literal_node(first)
    if literal is not None:
        return locate(literal)
    if report_lineno == call.lineno:
        return locate(call)
    return {}


def _check_raw_sql(*, module: Module) -> list[Violation]:
    """SQL-001: only flag raw SQL that actually reaches a DB execution sink."""
    relative_file = module.relative
    if "alembic" in relative_file or Path(relative_file).name.startswith("test_"):
        return []
    constants = _collect_string_constants(module=module)
    violations: list[Violation] = []
    for node in module.index.collect(ast.Call):
        kind = _classify_sink(node)
        if kind is None or not node.args:
            continue
        first = node.args[0]
        sql, interp = _extract_sql_string(first, constants=constants)
        if sql is None or not _SQL_KEYWORDS_RE.search(sql):
            continue
        # Report at the literal's line where possible (the SQL text); for
        # Name references and text(Name) wrappers, point at the assignment
        # line in the constants map; otherwise fall back to the call site.
        report_lineno = _find_literal_lineno(first, constants=constants) or node.lineno
        anchor_span = _build_finding_span(first=first, call=node, report_lineno=report_lineno)
        if interp:
            violations.append(
                Violation(
                    file=relative_file,
                    line=report_lineno,
                    rule="SQL-001: No raw SQL — use an ORM or parameterized queries",
                    message="f-string interpolation into SQL is an injection vector",
                    fix="Bind the value as a parameter instead of interpolating it into the SQL text",
                    **anchor_span,
                ),
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
                **anchor_span,
            ),
        )
    return violations


@dataclass
class SecurityPatternsCheck:
    """Validates web-shaped security patterns: auth dependency and raw SQL."""

    name: str = "security_patterns"
    description: str = "Web-security checks: auth dependency and raw SQL"
    source_root: str = ""
    rules: list[str] = field(
        default_factory=lambda: [
            "AUTHN-001: Mutation endpoints must have auth dependency",
            "SQL-001: No raw SQL — use an ORM or parameterized queries",
        ],
    )
    settings_keys: ClassVar[frozenset[str]] = frozenset({"source_root"})

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.security_patterns]`` configuration."""
        self.source_root = (
            read_str(settings=settings, key="source_root", default=self.source_root)
            .replace("\\", "/")
            .strip("/")
        )

    def _resolve_layer_relative(self, *, relative_file: str) -> str:
        """Re-anchor *relative_file* at the architectural source root.

        ``source_root`` is written relative to the project root ("src/myapp"),
        so under a src layout the ``api/`` layer is reached only after that
        prefix. This check is file-scoped, though, so a per-directory region can
        hand it a ``src_root`` that already sits inside the package; stripping
        the prefix when it is present, rather than demanding every file live
        under it, keeps the ``api/`` gate working from either anchor.
        """
        prefix = f"{self.source_root}/"
        if self.source_root and relative_file.startswith(prefix):
            return relative_file[len(prefix) :]
        return relative_file

    def run(self, *, src_root: str) -> CheckResult:
        """Scan all Python files under src/ for security violations."""
        violations: list[Violation] = []
        warnings: list[Violation] = []

        for module in iter_parsed_modules(Path(src_root)):
            relative_file = module.relative

            try:
                # AUTHN-001: Only check endpoint files (api/ layer).
                file_violations: list[Violation] = []
                if self._resolve_layer_relative(relative_file=relative_file).startswith("api/"):
                    file_violations.extend(_check_auth_on_mutations(module=module))

                # SQL-001: Check all files for raw SQL (except alembic).
                file_violations.extend(_check_raw_sql(module=module))
            except RecursionError:
                # A long ``"a" + "a" + ...`` chain makes _sql_from_binop and
                # _extract_sql_string recurse on BinOp.left/.right until the stack
                # overflows. Skip the file rather than crash the whole run.
                warnings.append(
                    build_skip_notice(
                        prefix="SQL",
                        file=relative_file,
                        name=module.path.name,
                        reason=TOO_DEEP,
                    ),
                )
                continue

            violations.extend(file_violations)

        return CheckResult.from_findings(check=self.name, violations=violations, warnings=warnings)


# Self-register on import.
register(SecurityPatternsCheck())
