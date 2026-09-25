"""NAMING-001 through NAMING-004: Naming convention enforcement.

Checks:
    NAMING-001  Repository methods (files under infrastructure/repositories/ or
                infrastructure/persistence/) that use a synonym prefix
                (fetch_/retrieve_/find_/remove_/add_) are steered to the CRUD
                equivalent get_/create_/update_/delete_/list_ (opt-in:
                [tool.lanorme.naming_consistency] repo_crud = true)
    NAMING-002  Service methods (files under application/services/) likewise
                steered off synonym prefixes (opt-in: service_crud = true)
    NAMING-003  Endpoint handlers should match HTTP verb (warning only)
    NAMING-004  Boolean functions should use is_/has_/can_/should_ prefixes (warning only)

Run:
    lanorme check . --check=naming_consistency
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import is_flag_set
from lanorme.checks.naming_shapes import Definition, is_framework_named
from lanorme.checks.naming_words import is_predicate, split_name
from lanorme.sources import Module, iter_parsed_modules, locate

# Allowed public method prefixes for repositories and services.
ALLOWED_PREFIXES = ("get_", "create_", "update_", "delete_", "list_")

# Forbidden prefixes with their suggested replacement.
FORBIDDEN_PREFIX_MAP: dict[str, str] = {
    "fetch_": "get_",
    "retrieve_": "get_",
    "find_": "get_",
    "remove_": "delete_",
    "add_": "create_",
}

# Expected handler prefix per HTTP method (NAMING-003).
HTTP_VERB_PREFIXES: dict[str, tuple[str, ...]] = {
    "get": ("get_", "list_"),
    "post": ("create_",),
    "put": ("update_",),
    "delete": ("delete_",),
}

# Endpoint names that are exempt from verb-matching: auth-issuance flows and
# health/readiness probes, which do not follow CRUD verb conventions.
VERB_EXEMPT_ENDPOINTS = frozenset(
    {
        # Auth-issuance handlers.
        "login",
        "logout",
        "refresh",
        "token",
        # Health probes.
        "health",
        "health_check",
        "ready",
        "readiness_check",
    },
)

# Boolean-appropriate prefixes (NAMING-004), the ones the fix names. A name
# passes when it reads as an assertion by any of the predicate shapes in
# ``naming_words.is_predicate``: an auxiliary anywhere (``is_``, ``has_``,
# ``can_``, ``should_``, ``does_``, ``user_is_active``), a third-person verb in
# front (``exists``, ``matches``, ``contains``), or a fused ``isdir``.
BOOL_PREFIXES = ("is_", "has_", "can_", "should_")

# Decorators that exempt a bool-returning function from NAMING-004: property
# and cached_property read as attributes (nouns), and staticmethod factories
# follow the enclosing class's vocabulary. Matched by bare name so dotted
# forms such as functools.cached_property are covered too.
BOOL_EXEMPT_DECORATORS = frozenset({"property", "cached_property", "staticmethod"})

# Leading verbs stripped before templating the NAMING-004 fix, so that
# 'check_auth_posture' suggests 'is_auth_posture' rather than the mangled
# 'is_check_auth_posture'.
BOOL_FIX_VERB_PREFIXES = ("check_", "get_", "compute_", "fetch_", "build_", "make_", "run_")

# Directories (relative to the source root) to scan for each rule.
REPO_DIRS = ("infrastructure/repositories", "infrastructure/persistence")
SERVICE_DIRS = ("application/services",)
ENDPOINT_DIRS = ("api/v1/endpoints",)


def _extract_public_class_methods(
    *,
    module: Module,
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Extract public methods from all classes in a module.

    Returns:
        List of (class_name, method_node) tuples for public methods.
    """
    results: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in module.index.collect(ast.ClassDef):
        for item in node.body:
            if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if item.name.startswith("_"):
                continue
            results.append((node.name, item))
    return results


def _check_forbidden_prefixes(
    *,
    method_name: str,
) -> tuple[str, str] | None:
    """Check if a method uses a forbidden prefix.

    Returns:
        (forbidden_prefix, suggested_prefix) if found, else None.
    """
    for forbidden, suggested in FORBIDDEN_PREFIX_MAP.items():
        if method_name.startswith(forbidden):
            return (forbidden, suggested)
    return None


def _check_repo_and_service_naming(
    *,
    module: Module,
    rule: str,
    layer_label: str,
) -> list[Violation]:
    """NAMING-001 / NAMING-002: Check method prefixes on classes."""
    violations: list[Violation] = []
    methods = _extract_public_class_methods(module=module)

    for class_name, method in methods:
        method_name = method.name
        match = _check_forbidden_prefixes(method_name=method_name)
        if match is None:
            continue
        forbidden, suggested = match
        violations.append(
            Violation(
                file=module.relative,
                line=method.lineno,
                rule=rule,
                message=(
                    f"{layer_label} method '{class_name}.{method_name}' "
                    f"uses forbidden prefix '{forbidden}'"
                ),
                fix=f"Rename to '{suggested}{method_name[len(forbidden) :]}'",
                **locate(method),
            ),
        )

    return violations


def _extract_http_method_from_decorator(
    *,
    decorator: ast.expr,
) -> str | None:
    """Extract the HTTP method from a @router.<method>(...) decorator."""
    if not isinstance(decorator, ast.Call):
        return None
    if not isinstance(decorator.func, ast.Attribute):
        return None
    method = decorator.func.attr
    if method in HTTP_VERB_PREFIXES:
        return method
    return None


def _check_endpoint_verb_naming(*, module: Module) -> list[Violation]:
    """NAMING-003: Endpoint handler names should match HTTP verb."""
    warnings: list[Violation] = []

    for node in module.index.functions:
        if node.name in VERB_EXEMPT_ENDPOINTS:
            continue

        for decorator in node.decorator_list:
            http_method = _extract_http_method_from_decorator(decorator=decorator)
            if http_method is None:
                continue

            expected_prefixes = HTTP_VERB_PREFIXES[http_method]
            if not any(node.name.startswith(prefix) for prefix in expected_prefixes):
                expected_str = " or ".join(f"'{p}'" for p in expected_prefixes)
                warnings.append(
                    Violation(
                        file=module.relative,
                        line=node.lineno,
                        rule=(
                            f"NAMING-003: @router.{http_method} handler "
                            f"should use {expected_str} prefix"
                        ),
                        message=(
                            f"Handler '{node.name}' is a {http_method.upper()} endpoint "
                            f"but does not start with {expected_str}"
                        ),
                        fix=(
                            f"Rename to '{expected_prefixes[0]}{node.name}' "
                            f"or add '{node.name}' to VERB_EXEMPT_ENDPOINTS if intentional"
                        ),
                        **locate(node),
                    ),
                )
            break  # Only check the first matching decorator per function.

    return warnings


def _has_bool_return_annotation(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function has a -> bool return type annotation."""
    annotation = node.returns
    if annotation is None:
        return False
    if isinstance(annotation, ast.Name) and annotation.id == "bool":
        return True
    return isinstance(annotation, ast.Constant) and annotation.value == "bool"


def _has_bool_exempt_decorator(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function carries a decorator that exempts it from NAMING-004."""
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id in BOOL_EXEMPT_DECORATORS:
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr in BOOL_EXEMPT_DECORATORS:
            return True
    return False


def _is_protocol_base(*, base: ast.expr) -> bool:
    """Check if a class base refers to Protocol (bare, dotted, or subscripted)."""
    target = base.value if isinstance(base, ast.Subscript) else base
    if isinstance(target, ast.Name) and target.id == "Protocol":
        return True
    return isinstance(target, ast.Attribute) and target.attr == "Protocol"


def _collect_protocol_members(*, module: Module) -> set[int]:
    """Collect node ids of functions defined directly inside Protocol classes."""
    # ast.walk yields nodes without parent links, so Protocol membership is
    # resolved up front: any function in this skip set belongs to a class whose
    # bases include Protocol (e.g. Protocol, typing.Protocol, Protocol[T]).
    members: set[int] = set()
    for node in module.index.collect(ast.ClassDef):
        if not any(_is_protocol_base(base=base) for base in node.bases):
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                members.add(id(item))
    return members


def _map_method_owners(*, module: Module) -> dict[int, ast.ClassDef]:
    """The class each method is defined directly in, keyed by the method node's id."""
    owners: dict[int, ast.ClassDef] = {}
    for node in module.index.collect(ast.ClassDef):
        for item in node.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                owners[id(item)] = node
    return owners


def _suggest_bool_rename(*, name: str) -> str:
    """Suggest a boolean-prefixed rename, stripping a leading verb if present."""
    suggested = name
    for verb in BOOL_FIX_VERB_PREFIXES:
        if name.startswith(verb) and len(name) > len(verb):
            suggested = name[len(verb) :]
            break
    return f"Rename to 'is_{suggested}' or another boolean prefix (has_, can_, should_)"


def _check_bool_naming(*, module: Module) -> list[Violation]:
    """NAMING-004: Boolean functions should use is_/has_/can_/should_ prefix."""
    warnings: list[Violation] = []
    protocol_members = _collect_protocol_members(module=module)
    owners = _map_method_owners(module=module)

    for node in module.index.functions:
        if node.name.startswith("_"):
            continue

        if id(node) in protocol_members:
            continue

        if _has_bool_exempt_decorator(node=node):
            continue

        if not _has_bool_return_annotation(node=node):
            continue

        if is_predicate(tokens=split_name(name=node.name)):
            continue

        # A name a protocol or framework fixed (``readable``, ``filter`` on a
        # logging.Filter, Django's ``allow_migrate``, Qt's ``eventFilter``).
        if is_framework_named(definition=Definition(node=node, owner=owners.get(id(node)))):
            continue

        warnings.append(
            Violation(
                file=module.relative,
                line=node.lineno,
                rule="NAMING-004: Boolean functions should use is_/has_/can_/should_ prefix",
                message=f"Function '{node.name}' returns bool but lacks a boolean prefix",
                fix=_suggest_bool_rename(name=node.name),
                **locate(node),
            ),
        )

    return warnings


def _file_is_under(*, relative_path: str, directories: tuple[str, ...]) -> bool:
    """True if the path passes through one of the layout directories.

    Matched at any depth, so a ``src/`` layout or a nested region's package
    (``strict/infrastructure/repositories/``) is recognised without a
    ``source_root`` setting.
    """
    normalized = "/" + relative_path.replace("\\", "/")
    return any(f"/{d}/" in normalized for d in directories)


@dataclass
class NamingConsistencyCheck:
    """Validates naming conventions for repositories, services, endpoints, and booleans."""

    name: str = "naming_consistency"
    description: str = "Naming convention enforcement (methods, endpoints, booleans)"
    # NAMING-001 / NAMING-002 (CRUD prefixes) ship default-off: the audit
    # surfaced that they actively suppress the ubiquitous-language verbs
    # the TERM check exists to protect (approve_loan, transfer_funds, etc.).
    # Opt in via [tool.lanorme.naming_consistency] repo_crud = true / service_crud = true.
    repo_crud: bool = False
    service_crud: bool = False
    rules: list[str] = field(
        default_factory=lambda: [
            "NAMING-001: Repository methods must use get_/create_/update_/delete_/list_ prefixes (opt-in)",
            "NAMING-002: Service methods must use get_/create_/update_/delete_/list_ prefixes (opt-in)",
            "NAMING-003: Endpoint handlers should match HTTP verb (warning)",
            "NAMING-004: Boolean functions should use is_/has_/can_/should_ prefix (warning)",
        ],
    )
    opt_in_rules: ClassVar[frozenset[str]] = frozenset({"NAMING-001", "NAMING-002"})
    opt_in_settings: ClassVar[dict[str, str]] = {
        "NAMING-001": "repo_crud",
        "NAMING-002": "service_crud",
    }
    settings_keys: ClassVar[frozenset[str]] = frozenset({"repo_crud", "service_crud"})

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.naming_consistency]`` configuration."""
        self.repo_crud = is_flag_set(settings=settings, key="repo_crud", default=self.repo_crud)
        self.service_crud = is_flag_set(
            settings=settings,
            key="service_crud",
            default=self.service_crud,
        )

    def run(self, *, src_root: str) -> CheckResult:
        """Scan source files and validate naming conventions."""
        violations: list[Violation] = []
        warnings: list[Violation] = []

        for module in iter_parsed_modules(Path(src_root)):
            relative_file = module.relative

            # NAMING-001: Repository method naming (opt-in; conflicts with DDD ubiquitous-language).
            if self.repo_crud and _file_is_under(
                relative_path=relative_file,
                directories=REPO_DIRS,
            ):
                violations.extend(
                    _check_repo_and_service_naming(
                        module=module,
                        rule="NAMING-001: Repository methods must use get_/create_/update_/delete_/list_ prefixes",
                        layer_label="Repository",
                    ),
                )

            # NAMING-002: Service method naming (opt-in; conflicts with DDD ubiquitous-language).
            if self.service_crud and _file_is_under(
                relative_path=relative_file,
                directories=SERVICE_DIRS,
            ):
                violations.extend(
                    _check_repo_and_service_naming(
                        module=module,
                        rule="NAMING-002: Service methods must use get_/create_/update_/delete_/list_ prefixes",
                        layer_label="Service",
                    ),
                )

            # NAMING-003: Endpoint handler verb matching (warning only).
            if _file_is_under(relative_path=relative_file, directories=ENDPOINT_DIRS):
                warnings.extend(_check_endpoint_verb_naming(module=module))

            # NAMING-004: Boolean function prefixes (warning only, all files).
            warnings.extend(_check_bool_naming(module=module))

        return CheckResult.from_findings(check=self.name, violations=violations, warnings=warnings)


# Self-register on import.
register(NamingConsistencyCheck())
