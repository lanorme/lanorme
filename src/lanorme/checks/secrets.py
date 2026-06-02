"""SECRETPY-001: hardcoded secrets in Python source.

Three detection paths, each precision-first (the multi-reviewer audit's stated
priority for security rules: do not produce a false sense of security):

1. **Named credential assigns**: ``password = "..."``, ``api_key = "..."``,
   dict-literal keys (``{"password": "..."}``), call kwargs
   (``connect(password="...")``). The value must be a real-looking string
   literal (length >= 8, no placeholder markers, or high entropy enough to
   defeat ``EXAMPLE`` / ``YOUR_`` markers in the body).
2. **Shape-only matches**: PEM private-key blocks, JWT-shaped tokens,
   ``Bearer`` header literals, DB / cache URLs with embedded
   ``user:pass@host`` credentials, and vendor-prefixed credentials (AWS AKIA
   / ASIA, GitHub ``ghp_`` / ``gho_`` / ``github_pat_``, Slack ``xox*``,
   Stripe ``sk_live_`` / ``sk_test_``). These betray themselves regardless of
   where they sit.
3. **Implicit exclusions**: files matching ``conftest.py``, ``seed_dev.py``,
   or starting with ``test_`` are skipped wholesale; names whose first segment
   is ``help_`` / ``hint_`` / ``msg_`` / etc. are documentation; names whose
   last segment is structural (``pattern``, ``endpoint``, ``header``,
   ``name``, ``len``, ...) are not credentials.

Scope is Python source only; ``.env`` / ``*.yaml`` / ``*.ipynb`` / ``*.tf``
are out of scope until a separate non-Python rule lands. The rule code is
``SECRETPY-001`` to make the scope explicit in the lint output.

Run:
    lanorme check . --check=secrets
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.discovery import iter_py_files

# A name suggests a credential when (i) it matches one of these multi-segment
# phrases as the whole name or as a ``_``-anchored suffix, OR (ii) one of its
# ``_``-separated segments is a bare credential token.
_CRED_NAME_PHRASES = frozenset({
    "api_key", "apikey",
    "access_key", "access_key_id",
    "secret_key", "secret_access_key",
    "private_key", "ssh_private_key", "signing_key", "encryption_key",
    "client_secret", "oauth_secret", "jwt_secret", "auth_secret", "signing_secret",
    "aws_access_key", "aws_access_key_id",
    "aws_secret_key", "aws_secret_access_key", "aws_session_token",
    "session_token", "access_token", "refresh_token", "bearer_token", "auth_token",
    "github_token", "github_pat", "slack_token",
})
_CRED_TOKEN_SEGMENTS = frozenset({
    "password", "passwd", "pwd",
    "secret", "token", "jwt", "passphrase", "apikey",
})
_NON_CRED_NAME_PREFIXES = (
    "help_", "hint_", "msg_", "prompt_", "description_", "example_", "usage_",
    "label_", "docs_", "info_", "title_", "placeholder_", "tooltip_",
)
_NON_CRED_LAST_SEGMENTS = frozenset({
    "help", "hint", "msg", "prompt", "description", "example", "usage",
    "label", "docs", "info", "title", "placeholder", "tooltip",
    "pattern", "regex", "re", "pat",
    "endpoint", "header", "name", "path", "url", "uri",
    "format", "kind", "type", "len", "length", "max", "min", "fmt",
    "field", "column", "default", "alias",
})
# Substrings in the value that mark a placeholder rather than a real secret.
# Excludes ``fake`` / ``dummy``: an attacker labelling a high-entropy literal
# ``fake-token-xyz`` is not a reason to skip it.
_PLACEHOLDER_MARKERS = (
    "<", "your-", "your_", "replace", "change", "example",
    "placeholder", "xxxxx", "*****", "tbd", "todo", "_here", "fixme",
    "redacted", "sample",
)

_SCAN_EXCLUDES = {"conftest.py", "seed_dev.py"}

_PEM_BLOCK_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_URL_WITH_CREDS_RE = re.compile(
    r"\b(postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|amqp|amqps)"
    r"://[^:/?#@]*:[^@/?#]+@"
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}=*")
# Vendor-prefixed credential shapes. Length thresholds keep them past common
# placeholders like ``sk_test_REPLACE`` / ``xoxb-REPLACE_THIS``.
_VENDOR_TOKEN_PATTERNS = (
    (re.compile(r"\bAKIA[A-Z0-9]{16}\b"), "AWS access-key ID literal (AKIA...)"),
    (re.compile(r"\bASIA[A-Z0-9]{16}\b"), "AWS temporary access-key literal (ASIA...)"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub personal-access token (ghp_/gho_/...)"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"), "GitHub fine-grained PAT literal"),
    (re.compile(r"\bxox[abps]-[A-Za-z0-9-]{20,}\b"), "Slack token literal (xox...)"),
    (re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{24,}\b"), "Stripe API key literal"),
)

_MIN_CRED_LITERAL_LEN = 8
_HIGH_ENTROPY_LEN = 32

_RULE = "SECRETPY-001: No hardcoded secrets in source code"
_FIX = "Read the value from an environment variable, secrets manager, or settings module"

_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"})


def _normalise_name(name: str) -> str:
    return name.lower().replace("-", "_")


def _name_is_credential(name: str) -> bool:
    """True if *name* (var / dict key / kwarg) looks like a credential holder."""
    norm = _normalise_name(name)
    if norm.startswith(_NON_CRED_NAME_PREFIXES):
        return False
    segments = norm.split("_")
    if not segments or segments[-1] in _NON_CRED_LAST_SEGMENTS:
        return False
    for phrase in _CRED_NAME_PHRASES:
        if norm == phrase or norm.endswith("_" + phrase):
            return True
    return any(seg in _CRED_TOKEN_SEGMENTS for seg in segments)


def _value_looks_high_entropy(text: str) -> bool:
    """True when *text* has enough variety to be a real key rather than a placeholder."""
    if len(text) < _HIGH_ENTROPY_LEN:
        return False
    has_upper = any(c.isupper() for c in text)
    has_lower = any(c.islower() for c in text)
    has_digit = any(c.isdigit() for c in text)
    return has_upper and has_lower and has_digit


def _value_is_real_secret(value: ast.expr) -> str | None:
    """Return the string content of *value* if it looks like a real secret, else ``None``."""
    if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
        return None
    text = value.value
    if len(text) < _MIN_CRED_LITERAL_LEN:
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        if not _value_looks_high_entropy(text):
            return None
    return text


def _violation(*, file: str, lineno: int, message: str) -> Violation:
    return Violation(file=file, line=lineno, rule=_RULE, message=message, fix=_FIX)


def _flag_assignment(
    *, name: str, value: ast.expr, lineno: int, file: str
) -> Violation | None:
    """Flag ``<credname> = "<literal>"`` style bindings."""
    if not _name_is_credential(name):
        return None
    if _value_is_real_secret(value) is None:
        return None
    return _violation(file=file, lineno=lineno, message=f"Hardcoded credential value bound to '{name}'")


def _shape_violation(*, value: str, lineno: int, file: str) -> Violation | None:
    """Flag SECRET-shape literals that betray themselves regardless of variable name."""
    if _PEM_BLOCK_RE.search(value):
        return _violation(file=file, lineno=lineno, message="PEM-formatted private key in source")
    if _JWT_RE.search(value):
        return _violation(file=file, lineno=lineno, message="JWT-shaped token literal in source")
    if _URL_WITH_CREDS_RE.search(value):
        return _violation(file=file, lineno=lineno, message="Database / cache URL with embedded credentials")
    if _BEARER_RE.search(value):
        return _violation(file=file, lineno=lineno, message="Bearer-token literal in source")
    for pattern, description in _VENDOR_TOKEN_PATTERNS:
        if pattern.search(value):
            return _violation(file=file, lineno=lineno, message=description)
    return None


def _from_assign(node: ast.Assign, *, file: str) -> list[Violation]:
    found: list[Violation] = []
    for target in node.targets:
        if not isinstance(target, ast.Name):
            continue
        hit = _flag_assignment(name=target.id, value=node.value, lineno=node.lineno, file=file)
        if hit is not None:
            found.append(hit)
    return found


def _from_annassign(node: ast.AnnAssign, *, file: str) -> list[Violation]:
    if not isinstance(node.target, ast.Name) or node.value is None:
        return []
    hit = _flag_assignment(name=node.target.id, value=node.value, lineno=node.lineno, file=file)
    return [hit] if hit is not None else []


def _from_dict(node: ast.Dict, *, file: str) -> list[Violation]:
    found: list[Violation] = []
    for key, value in zip(node.keys, node.values, strict=False):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            continue
        hit = _flag_assignment(name=key.value, value=value, lineno=key.lineno, file=file)
        if hit is not None:
            found.append(hit)
    return found


def _from_call_kwargs(node: ast.Call, *, file: str) -> list[Violation]:
    found: list[Violation] = []
    for kw in node.keywords:
        if kw.arg is None:
            continue
        hit = _flag_assignment(name=kw.arg, value=kw.value, lineno=kw.value.lineno, file=file)
        if hit is not None:
            found.append(hit)
    return found


def _from_string_constant(node: ast.Constant, *, file: str) -> list[Violation]:
    if not isinstance(node.value, str):
        return []
    hit = _shape_violation(value=node.value, lineno=node.lineno, file=file)
    return [hit] if hit is not None else []


def _scan_tree(*, tree: ast.AST, file: str) -> list[Violation]:
    found: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            found.extend(_from_assign(node, file=file))
        elif isinstance(node, ast.AnnAssign):
            found.extend(_from_annassign(node, file=file))
        elif isinstance(node, ast.Dict):
            found.extend(_from_dict(node, file=file))
        elif isinstance(node, ast.Call):
            found.extend(_from_call_kwargs(node, file=file))
        elif isinstance(node, ast.Constant):
            found.extend(_from_string_constant(node, file=file))
    deduped: dict[tuple[int, str], Violation] = {}
    for v in found:
        deduped.setdefault((v.line, v.message), v)
    return list(deduped.values())


@dataclass
class SecretsCheck:
    """Hardcoded secrets in Python source: assignments, dict keys, kwargs, and shapes."""

    name: str = "secrets"
    description: str = "Hardcoded secrets in Python source (SECRETPY-001)"
    rules: list[str] = field(
        default_factory=lambda: [
            "SECRETPY-001: No hardcoded secrets in source code",
        ]
    )

    def run(self, *, src_root: str) -> CheckResult:
        violations: list[Violation] = []
        root = Path(src_root)
        for path in iter_py_files(root):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            file_name = path.name
            if file_name in _SCAN_EXCLUDES or file_name.startswith("test_"):
                continue
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            relative = str(path.relative_to(root))
            violations.extend(_scan_tree(tree=tree, file=relative))
        status = Status.FAIL if violations else Status.PASS
        return CheckResult(check=self.name, status=status, violations=violations)


register(SecretsCheck())
