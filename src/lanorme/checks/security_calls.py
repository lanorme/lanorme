"""SHELL-001 through DEBUG-001: dangerous-call detection.

Six one-AST-node rules covering the most devastating bug classes a static
analyser can flag at zero context. The multi-reviewer audit
(``docs/audit/03-security-ops.md``) ranked each of these as a higher-value
target than the existing ``SQL-001``: every rule here catches a single,
well-known shape and refuses to fire on anything ambiguous.

    SHELL-001     subprocess/os shell-injection vectors
    DESERIAL-001  pickle / marshal / yaml.load on potentially untrusted data
    EVAL-001      eval / exec / compile on a non-literal argument
    CRYPTO-001    weak hash (md5 / sha1 for security) or deprecated TLS protocols
    TLS-001       certificate verification disabled
    DEBUG-001     debug mode enabled in a web framework

Precision-first: when the AST shape is ambiguous, the rule prefers a false
negative over a false positive (the security reviewer's stated priority: do
not produce a false sense of security). Use ``# noqa: <CODE>`` to silence a
legitimate use, and ``[tool.lanorme.per-file-ignores]`` to silence broader
patches (e.g. trusted-input ``pickle.load`` inside an internal cache module).

Run:
    lanorme check . --check=security_calls
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register

_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"})


def _attr_chain(node: ast.AST) -> tuple[str, ...]:
    """Return the dotted attribute chain at *node*, or () if it isn't one.

    ``hashlib.md5`` -> ('hashlib', 'md5'); ``ssl.PROTOCOL_TLSv1`` -> ('ssl',
    'PROTOCOL_TLSv1'); ``client.x.execute`` -> ('client', 'x', 'execute');
    anything else (subscripts, calls in the chain, etc.) -> ().
    """
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return tuple(reversed(parts))
    return ()


def _kwarg_named(*, call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _is_constant_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_constant_false(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


# --- SHELL-001 ------------------------------------------------------------ #

_SUBPROCESS_FUNCS = frozenset({"run", "call", "check_call", "check_output", "Popen"})


def _shell_violations(*, call: ast.Call, relative_file: str) -> list[Violation]:
    chain = _attr_chain(call.func)
    if chain == ("os", "system") or chain == ("os", "popen"):
        return [
            Violation(
                file=relative_file,
                line=call.lineno,
                rule="SHELL-001",
                message=f"{'.'.join(chain)} runs the argument through the shell",
                fix="Use subprocess.run([...], shell=False) with an argv list instead",
            )
        ]
    if len(chain) == 2 and chain[0] == "subprocess" and chain[1] in _SUBPROCESS_FUNCS:
        if _is_constant_true(_kwarg_named(call=call, name="shell")):
            return [
                Violation(
                    file=relative_file,
                    line=call.lineno,
                    rule="SHELL-001",
                    message=f"subprocess.{chain[1]}(..., shell=True) runs the argument through the shell",
                    fix="Drop shell=True and pass the command as a list of arguments",
                )
            ]
    return []


# --- DESERIAL-001 --------------------------------------------------------- #

_DESERIAL_PAIRS = frozenset(
    {
        ("pickle", "loads"),
        ("pickle", "load"),
        ("marshal", "loads"),
        ("marshal", "load"),
        ("dill", "loads"),
        ("dill", "load"),
        ("cPickle", "loads"),
        ("cPickle", "load"),
    }
)
_SAFE_YAML_LOADERS = frozenset({"SafeLoader", "CSafeLoader", "BaseLoader"})


def _deserial_violations(*, call: ast.Call, relative_file: str) -> list[Violation]:
    chain = _attr_chain(call.func)
    if len(chain) == 2 and chain in _DESERIAL_PAIRS:
        return [
            Violation(
                file=relative_file,
                line=call.lineno,
                rule="DESERIAL-001",
                message=f"{'.'.join(chain)} on untrusted input is an RCE primitive",
                fix="Replace with a safe serialiser (json, msgpack), or # noqa: DESERIAL-001 if the input is trusted",
            )
        ]
    if chain == ("yaml", "load"):
        loader = _kwarg_named(call=call, name="Loader")
        loader_chain = _attr_chain(loader) if loader is not None else ()
        loader_ok = (
            (loader_chain and loader_chain[-1] in _SAFE_YAML_LOADERS)
            or (isinstance(loader, ast.Name) and loader.id in _SAFE_YAML_LOADERS)
        )
        if not loader_ok:
            return [
                Violation(
                    file=relative_file,
                    line=call.lineno,
                    rule="DESERIAL-001",
                    message="yaml.load without Loader=SafeLoader is an RCE primitive",
                    fix="Use yaml.safe_load(...) or pass Loader=yaml.SafeLoader explicitly",
                )
            ]
    if chain == ("yaml", "unsafe_load"):
        return [
            Violation(
                file=relative_file,
                line=call.lineno,
                rule="DESERIAL-001",
                message="yaml.unsafe_load constructs arbitrary Python objects",
                fix="Use yaml.safe_load(...) instead",
            )
        ]
    return []


# --- EVAL-001 ------------------------------------------------------------- #

_EVAL_FUNCS = frozenset({"eval", "exec", "compile"})


def _eval_violations(*, call: ast.Call, relative_file: str) -> list[Violation]:
    if not isinstance(call.func, ast.Name) or call.func.id not in _EVAL_FUNCS:
        return []
    if not call.args:
        return []
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return []  # literal argument: common in trusted compile() flows
    return [
        Violation(
            file=relative_file,
            line=call.lineno,
            rule="EVAL-001",
            message=f"{call.func.id}() on a non-literal argument is an RCE primitive",
            fix="Use ast.literal_eval for trusted-shape parsing, or build a dispatch table",
        )
    ]


# --- CRYPTO-001 ----------------------------------------------------------- #

_WEAK_HASH_NAMES = frozenset({"md5", "sha1"})
_WEAK_TLS_CONSTANTS = frozenset(
    {"PROTOCOL_SSLv2", "PROTOCOL_SSLv3", "PROTOCOL_TLSv1", "PROTOCOL_TLSv1_1"}
)


def _crypto_call_violations(*, call: ast.Call, relative_file: str) -> list[Violation]:
    chain = _attr_chain(call.func)
    if chain and chain[0] == "hashlib" and len(chain) == 2 and chain[1] in _WEAK_HASH_NAMES:
        # hashlib.md5(..., usedforsecurity=False) declares non-security use.
        if _is_constant_false(_kwarg_named(call=call, name="usedforsecurity")):
            return []
        return [
            Violation(
                file=relative_file,
                line=call.lineno,
                rule="CRYPTO-001",
                message=f"hashlib.{chain[1]} is a weak hash for security purposes",
                fix="Use hashlib.sha256+ for security; pass usedforsecurity=False for non-security uses",
            )
        ]
    if chain == ("hashlib", "new") and call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.lower() in _WEAK_HASH_NAMES:
            return [
                Violation(
                    file=relative_file,
                    line=call.lineno,
                    rule="CRYPTO-001",
                    message=f"hashlib.new({first.value!r}) is a weak hash for security purposes",
                    fix="Use hashlib.new('sha256') or stronger",
                )
            ]
    return []


def _crypto_attribute_violations(*, node: ast.Attribute, relative_file: str) -> list[Violation]:
    chain = _attr_chain(node)
    if chain == ("ssl",) + chain[1:] and len(chain) == 2 and chain[1] in _WEAK_TLS_CONSTANTS:
        return [
            Violation(
                file=relative_file,
                line=node.lineno,
                rule="CRYPTO-001",
                message=f"ssl.{chain[1]} is a deprecated TLS protocol",
                fix="Use ssl.PROTOCOL_TLS_CLIENT (TLS 1.2+) or higher",
            )
        ]
    return []


# --- TLS-001 -------------------------------------------------------------- #

_TLS_CLIENT_MODULES = frozenset({"requests", "httpx", "aiohttp"})


def _tls_violations(*, call: ast.Call, relative_file: str) -> list[Violation]:
    chain = _attr_chain(call.func)
    if chain and chain[0] in _TLS_CLIENT_MODULES and _is_constant_false(_kwarg_named(call=call, name="verify")):
        return [
            Violation(
                file=relative_file,
                line=call.lineno,
                rule="TLS-001",
                message=f"{'.'.join(chain)}(..., verify=False) disables certificate verification",
                fix="Remove verify=False (or pin a CA bundle via verify=<path>) — MITM enabler in production",
            )
        ]
    if chain == ("ssl", "_create_unverified_context"):
        return [
            Violation(
                file=relative_file,
                line=call.lineno,
                rule="TLS-001",
                message="ssl._create_unverified_context disables certificate verification globally",
                fix="Use ssl.create_default_context() instead",
            )
        ]
    return []


def _tls_attribute_violations(*, node: ast.Attribute, relative_file: str) -> list[Violation]:
    chain = _attr_chain(node)
    if chain == ("ssl", "CERT_NONE"):
        return [
            Violation(
                file=relative_file,
                line=node.lineno,
                rule="TLS-001",
                message="ssl.CERT_NONE disables certificate verification when assigned to verify_mode",
                fix="Use ssl.CERT_REQUIRED (the default) and provide a trust store",
            )
        ]
    return []


# --- DEBUG-001 ------------------------------------------------------------ #

_WEB_FRAMEWORK_CONSTRUCTORS = frozenset({"Flask", "FastAPI"})


def _debug_violations(*, call: ast.Call, relative_file: str) -> list[Violation]:
    # Framework constructor with debug=True: Flask(__name__, debug=True), FastAPI(debug=True).
    constructor: str | None = None
    if isinstance(call.func, ast.Name) and call.func.id in _WEB_FRAMEWORK_CONSTRUCTORS:
        constructor = call.func.id
    chain = _attr_chain(call.func)
    if chain and chain[-1] in _WEB_FRAMEWORK_CONSTRUCTORS:
        constructor = chain[-1]
    if constructor is not None and _is_constant_true(_kwarg_named(call=call, name="debug")):
        return [
            Violation(
                file=relative_file,
                line=call.lineno,
                rule="DEBUG-001",
                message=f"{constructor}(debug=True) exposes the interactive debugger in production",
                fix="Set debug from an environment variable; default it to False",
            )
        ]
    # app.run(debug=True) / app.run_server(debug=True).
    if chain and chain[-1] in {"run", "run_server"} and _is_constant_true(_kwarg_named(call=call, name="debug")):
        return [
            Violation(
                file=relative_file,
                line=call.lineno,
                rule="DEBUG-001",
                message=f"{'.'.join(chain)}(debug=True) starts the server in debug mode",
                fix="Read debug from configuration; never hard-code True",
            )
        ]
    return []


# --- Module-level DEBUG = True in settings/config files ------------------- #

def _settings_assign_violations(*, node: ast.Assign, relative_file: str) -> list[Violation]:
    file_name = Path(relative_file).name.lower()
    if not (file_name.endswith("settings.py") or file_name.endswith("config.py")):
        return []
    if not _is_constant_true(node.value):
        return []
    found: list[Violation] = []
    for target in node.targets:
        if isinstance(target, ast.Name) and target.id == "DEBUG":
            found.append(
                Violation(
                    file=relative_file,
                    line=node.lineno,
                    rule="DEBUG-001",
                    message=f"DEBUG = True at module scope in {file_name}",
                    fix="Default DEBUG = False; flip it via an environment variable in development only",
                )
            )
    return found


# --- Check class ---------------------------------------------------------- #


@dataclass
class SecurityCallsCheck:
    """Six dangerous-call rules: shell, deserial, eval, weak crypto, TLS off, debug on."""

    name: str = "security_calls"
    description: str = "Dangerous-call detection (shell, deserial, eval, crypto, TLS, debug)"
    rules: list[str] = field(
        default_factory=lambda: [
            "SHELL-001: No subprocess shell=True / os.system / os.popen",
            "DESERIAL-001: No pickle/marshal/yaml.load on potentially untrusted input",
            "EVAL-001: No eval/exec/compile on a non-literal argument",
            "CRYPTO-001: No weak hash (md5/sha1 for security) or deprecated TLS protocol",
            "TLS-001: No verify=False or unverified SSL context",
            "DEBUG-001: No debug=True in a web-framework constructor or run() call",
        ]
    )

    def _scan_tree(self, *, tree: ast.AST, relative_file: str) -> list[Violation]:
        found: list[Violation] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                found.extend(_shell_violations(call=node, relative_file=relative_file))
                found.extend(_deserial_violations(call=node, relative_file=relative_file))
                found.extend(_eval_violations(call=node, relative_file=relative_file))
                found.extend(_crypto_call_violations(call=node, relative_file=relative_file))
                found.extend(_tls_violations(call=node, relative_file=relative_file))
                found.extend(_debug_violations(call=node, relative_file=relative_file))
            elif isinstance(node, ast.Attribute):
                found.extend(_crypto_attribute_violations(node=node, relative_file=relative_file))
                found.extend(_tls_attribute_violations(node=node, relative_file=relative_file))
            elif isinstance(node, ast.Assign):
                found.extend(_settings_assign_violations(node=node, relative_file=relative_file))
        return found

    def run(self, *, src_root: str) -> CheckResult:
        violations: list[Violation] = []
        root = Path(src_root)
        for path in sorted(root.rglob("*.py")):
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


register(SecurityCallsCheck())
