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

Every rule sees a call through the module's imports: ``import subprocess as
sp`` and ``from subprocess import run as sh`` resolve to ``subprocess.run``.
A name the module rebinds itself (a parameter, a local ``def``, an
assignment) is treated as unknown, so a local ``run(cmd, shell=True)`` or a
visitor's own ``eval(node)`` never fires. An ``ssl`` constant that is only
compared against (``if proto == ssl.PROTOCOL_TLSv1: reject()``) is a guard,
not a use.

Run:
    lanorme check . --check=security_calls
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Violation, register
from lanorme.sources import Module, iter_parsed_modules, locate

# (rule, message, fix) for one finding.
_Finding = tuple[str, str, str]

# Nodes that bind a plain name and so shadow an import or a builtin.
_BINDING_NODES = (
    ast.arg,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.ExceptHandler,
    ast.Assign,
    ast.AnnAssign,
    ast.For,
    ast.AsyncFor,
    ast.comprehension,
    ast.withitem,
    ast.NamedExpr,
)

_FIX_SHELL = "Use subprocess.run([...], shell=False) with an argv list instead"
_FIX_DESERIAL = "Replace with a safe serialiser (json, msgpack), or # noqa: DESERIAL-001 if the input is trusted"
_FIX_TLS = "Remove verify=False (or pin a CA bundle via verify=<path>) — MITM enabler in production"

# Calls flagged on their resolved dotted name alone.
_FLAGGED_CALLS: dict[tuple[str, ...], _Finding] = {
    ("os", "system"): ("SHELL-001", "os.system runs the argument through the shell", _FIX_SHELL),
    ("os", "popen"): ("SHELL-001", "os.popen runs the argument through the shell", _FIX_SHELL),
    ("yaml", "unsafe_load"): (
        "DESERIAL-001",
        "yaml.unsafe_load constructs arbitrary Python objects",
        "Use yaml.safe_load(...) instead",
    ),
    ("ssl", "_create_unverified_context"): (
        "TLS-001",
        "ssl._create_unverified_context disables certificate verification globally",
        "Use ssl.create_default_context() instead",
    ),
    **{
        (module, func): (
            "DESERIAL-001",
            f"{module}.{func} on untrusted input is an RCE primitive",
            _FIX_DESERIAL,
        )
        for module in ("pickle", "cPickle", "marshal", "dill")
        for func in ("load", "loads")
    },
}

# Attribute references flagged on their resolved dotted name alone.
_FLAGGED_ATTRIBUTES: dict[tuple[str, ...], _Finding] = {
    ("ssl", "CERT_NONE"): (
        "TLS-001",
        "ssl.CERT_NONE disables certificate verification when assigned to verify_mode",
        "Use ssl.CERT_REQUIRED (the default) and provide a trust store",
    ),
    **{
        ("ssl", name): (
            "CRYPTO-001",
            f"ssl.{name} is a deprecated TLS protocol",
            "Use ssl.PROTOCOL_TLS_CLIENT (TLS 1.2+) or higher",
        )
        for name in ("PROTOCOL_SSLv2", "PROTOCOL_SSLv3", "PROTOCOL_TLSv1", "PROTOCOL_TLSv1_1")
    },
}

_SUBPROCESS_FUNCS = frozenset({"run", "call", "check_call", "check_output", "Popen"})
_SAFE_YAML_LOADERS = frozenset({"SafeLoader", "CSafeLoader", "BaseLoader"})
_EVAL_FUNCS = frozenset({"eval", "exec", "compile"})
_WEAK_HASH_NAMES = frozenset({"md5", "sha1"})
# Per HTTP client, the keywords that switch certificate verification off.
_TLS_OFF_KWARGS = {
    "requests": ("verify",),
    "httpx": ("verify",),
    "aiohttp": ("ssl", "verify_ssl"),
}
_WEB_FRAMEWORK_CONSTRUCTORS = frozenset({"Flask", "FastAPI"})


def _extract_attr_chain(node: ast.AST | None) -> tuple[str, ...]:
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


def _find_kwarg_named(*, call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _is_constant_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_constant_false(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _is_string_literal(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _build_violation(*, node: ast.AST, finding: _Finding, file: str) -> Violation:
    rule, message, fix = finding
    return Violation(
        file=file,
        line=node.lineno,
        rule=rule,
        message=message,
        fix=fix,
        **locate(node),
    )


# --- Module scope: imports, rebinding, comparison operands ----------------- #


@dataclass(frozen=True)
class _Scope:
    """What a module's own text says about the names its calls use."""

    aliases: dict[str, tuple[str, ...]]
    rebound: frozenset[str]
    compared: frozenset[int]


def _iter_target_names(target: ast.AST | None) -> Iterator[str]:
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.Starred):
        yield from _iter_target_names(target.value)
    elif isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            yield from _iter_target_names(element)


def _iter_bound_names(node: ast.AST) -> Iterator[str]:
    """The plain names *node* binds: parameters, def/class names, targets."""
    if isinstance(node, ast.arg):
        yield node.arg
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        yield node.name
    elif isinstance(node, ast.ExceptHandler):
        if node.name:
            yield node.name
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            yield from _iter_target_names(target)
    elif isinstance(node, ast.withitem):
        yield from _iter_target_names(node.optional_vars)
    else:  # AnnAssign, For, AsyncFor, comprehension, NamedExpr
        yield from _iter_target_names(node.target)  # type: ignore[attr-defined]


def _collect_import_aliases(module: Module) -> dict[str, tuple[str, ...]]:
    """Names bound by absolute imports, mapped to the dotted target each stands for.

    ``import subprocess as sp`` -> ``{'sp': ('subprocess',)}``;
    ``from hashlib import md5 as digest`` -> ``{'digest': ('hashlib', 'md5')}``.
    Relative and star imports bind nothing here.
    """
    aliases: dict[str, tuple[str, ...]] = {}
    for node in module.index.collect(ast.Import):
        aliases.update(
            (alias.asname, tuple(alias.name.split("."))) for alias in node.names if alias.asname
        )
    for node in module.index.collect(ast.ImportFrom):
        if node.module and not node.level:
            prefix = tuple(node.module.split("."))
            aliases.update(
                (alias.asname or alias.name, (*prefix, alias.name))
                for alias in node.names
                if alias.name != "*"
            )
    return aliases


def _collect_compared_ids(module: Module) -> frozenset[int]:
    """Ids of the operands of every comparison, one level into a tuple / list / set."""
    ids: set[int] = set()
    for node in module.index.collect(ast.Compare):
        for operand in (node.left, *node.comparators):
            ids.add(id(operand))
            if isinstance(operand, ast.Tuple | ast.List | ast.Set):
                ids.update(id(element) for element in operand.elts)
    return frozenset(ids)


def _build_scope(module: Module) -> _Scope:
    rebound: set[str] = set()
    for node in module.index.collect(*_BINDING_NODES):
        rebound.update(_iter_bound_names(node))
    aliases = _collect_import_aliases(module)
    return _Scope(
        aliases={name: target for name, target in aliases.items() if name not in rebound},
        rebound=frozenset(rebound),
        compared=_collect_compared_ids(module),
    )


def _resolve_chain(node: ast.AST, *, scope: _Scope) -> tuple[str, ...]:
    """The attribute chain at *node* with its head import alias expanded."""
    chain = _extract_attr_chain(node)
    if chain and chain[0] in scope.aliases:
        return scope.aliases[chain[0]] + chain[1:]
    return chain


# --- Conditional rules: the name plus one argument shape ------------------ #


def _find_shell_finding(*, call: ast.Call, chain: tuple[str, ...]) -> _Finding | None:
    """SHELL-001: ``subprocess.<func>(..., shell=True)``."""
    if len(chain) != 2 or chain[0] != "subprocess" or chain[1] not in _SUBPROCESS_FUNCS:
        return None
    if not _is_constant_true(_find_kwarg_named(call=call, name="shell")):
        return None
    return (
        "SHELL-001",
        f"subprocess.{chain[1]}(..., shell=True) runs the argument through the shell",
        "Drop shell=True and pass the command as a list of arguments",
    )


def _find_deserial_finding(*, call: ast.Call, chain: tuple[str, ...]) -> _Finding | None:
    """DESERIAL-001: ``yaml.load`` without a safe Loader, by keyword or second positional."""
    if chain != ("yaml", "load"):
        return None
    loader = _find_kwarg_named(call=call, name="Loader")
    if loader is None and len(call.args) > 1:
        loader = call.args[1]
    loader_chain = _extract_attr_chain(loader)
    if loader_chain and loader_chain[-1] in _SAFE_YAML_LOADERS:
        return None
    return (
        "DESERIAL-001",
        "yaml.load without Loader=SafeLoader is an RCE primitive",
        "Use yaml.safe_load(...) or pass Loader=yaml.SafeLoader explicitly",
    )


def _is_eval_chain(chain: tuple[str, ...], *, scope: _Scope) -> bool:
    """True for the builtin ``eval`` / ``exec`` / ``compile``, bare or via ``builtins``."""
    if len(chain) == 2 and chain[0] == "builtins":
        return chain[1] in _EVAL_FUNCS
    return len(chain) == 1 and chain[0] in _EVAL_FUNCS and chain[0] not in scope.rebound


def _find_eval_finding(*, call: ast.Call, chain: tuple[str, ...], scope: _Scope) -> _Finding | None:
    """EVAL-001: the builtin on a non-literal first argument."""
    if not call.args or not _is_eval_chain(chain, scope=scope):
        return None
    if _is_string_literal(call.args[0]):
        return None  # literal argument: common in trusted compile() flows
    return (
        "EVAL-001",
        f"{chain[-1]}() on a non-literal argument is an RCE primitive",
        "Use ast.literal_eval for trusted-shape parsing, or build a dispatch table",
    )


def _find_weak_hash_name(*, call: ast.Call, chain: tuple[str, ...]) -> str | None:
    """The algorithm ``hashlib.md5(...)`` / ``hashlib.new("md5", ...)`` names, if weak."""
    if len(chain) != 2 or chain[0] != "hashlib":
        return None
    if chain[1] in _WEAK_HASH_NAMES:
        return chain[1]
    if chain[1] == "new" and call.args and _is_string_literal(call.args[0]):
        return call.args[0].value if call.args[0].value.lower() in _WEAK_HASH_NAMES else None
    return None


def _find_crypto_finding(*, call: ast.Call, chain: tuple[str, ...]) -> _Finding | None:
    """CRYPTO-001: a weak hash, unless ``usedforsecurity`` is False or not a literal."""
    algorithm = _find_weak_hash_name(call=call, chain=chain)
    if algorithm is None:
        return None
    flag = _find_kwarg_named(call=call, name="usedforsecurity")
    if flag is not None and not _is_constant_true(flag):
        return None  # declared non-security, or too ambiguous to call
    spelled = f"hashlib.new({algorithm!r})" if chain[1] == "new" else f"hashlib.{algorithm}"
    return (
        "CRYPTO-001",
        f"{spelled} is a weak hash for security purposes",
        "Use hashlib.sha256+ for security; pass usedforsecurity=False for non-security uses",
    )


def _find_tls_finding(*, call: ast.Call, chain: tuple[str, ...]) -> _Finding | None:
    """TLS-001: an HTTP client call with verification switched off by keyword."""
    for name in _TLS_OFF_KWARGS.get(chain[0], ()) if chain else ():
        if _is_constant_false(_find_kwarg_named(call=call, name=name)):
            return (
                "TLS-001",
                f"{'.'.join(chain)}(..., {name}=False) disables certificate verification",
                _FIX_TLS,
            )
    return None


def _find_debug_finding(*, call: ast.Call, chain: tuple[str, ...]) -> _Finding | None:
    """DEBUG-001: ``Flask(debug=True)`` / ``FastAPI(debug=True)`` / ``*.run(debug=True)``."""
    if not chain or not _is_constant_true(_find_kwarg_named(call=call, name="debug")):
        return None
    if chain[-1] in _WEB_FRAMEWORK_CONSTRUCTORS:
        return (
            "DEBUG-001",
            f"{chain[-1]}(debug=True) exposes the interactive debugger in production",
            "Set debug from an environment variable; default it to False",
        )
    if chain[-1] in {"run", "run_server"}:
        return (
            "DEBUG-001",
            f"{'.'.join(chain)}(debug=True) starts the server in debug mode",
            "Read debug from configuration; never hard-code True",
        )
    return None


def _find_settings_assign_violations(*, node: ast.Assign, file: str) -> list[Violation]:
    """DEBUG-001: module-level ``DEBUG = True`` in a ``*settings.py`` / ``*config.py``."""
    file_name = Path(file).name.lower()
    if not (file_name.endswith("settings.py") or file_name.endswith("config.py")):
        return []
    if not _is_constant_true(node.value):
        return []
    finding = (
        "DEBUG-001",
        f"DEBUG = True at module scope in {file_name}",
        "Default DEBUG = False; flip it via an environment variable in development only",
    )
    return [
        _build_violation(node=node, finding=finding, file=file)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "DEBUG"
    ]


_CALL_FINDERS = (
    _find_shell_finding,
    _find_deserial_finding,
    _find_crypto_finding,
    _find_tls_finding,
    _find_debug_finding,
)


def _find_call_violations(*, call: ast.Call, scope: _Scope, file: str) -> list[Violation]:
    chain = _resolve_chain(call.func, scope=scope)
    findings = [_FLAGGED_CALLS.get(chain), _find_eval_finding(call=call, chain=chain, scope=scope)]
    findings.extend(finder(call=call, chain=chain) for finder in _CALL_FINDERS)
    return [
        _build_violation(node=call, finding=finding, file=file)
        for finding in findings
        if finding is not None
    ]


def _find_attribute_violations(*, node: ast.Attribute, scope: _Scope, file: str) -> list[Violation]:
    if id(node) in scope.compared:
        return []  # ``if proto == ssl.PROTOCOL_TLSv1: reject()`` guards against the value
    finding = _FLAGGED_ATTRIBUTES.get(_resolve_chain(node, scope=scope))
    return [] if finding is None else [_build_violation(node=node, finding=finding, file=file)]


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
        ],
    )

    def _scan_module(self, *, module: Module) -> list[Violation]:
        found: list[Violation] = []
        file = module.relative
        scope = _build_scope(module)
        for node in module.index.collect(ast.Call, ast.Attribute, ast.Assign):
            if isinstance(node, ast.Call):
                found.extend(_find_call_violations(call=node, scope=scope, file=file))
            elif isinstance(node, ast.Attribute):
                found.extend(_find_attribute_violations(node=node, scope=scope, file=file))
            elif isinstance(node, ast.Assign):
                found.extend(_find_settings_assign_violations(node=node, file=file))
        return found

    def run(self, *, src_root: str) -> CheckResult:
        violations: list[Violation] = []
        for module in iter_parsed_modules(Path(src_root)):
            violations.extend(self._scan_module(module=module))
        return CheckResult.from_findings(check=self.name, violations=violations)


register(SecurityCallsCheck())
