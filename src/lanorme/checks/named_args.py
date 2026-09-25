"""KWARG-001: Enforce named arguments (bare ``*`` separator) on multi-parameter functions.

Every function or method with more than one non-self/cls parameter must use a
bare ``*`` separator so callers are forced to use keyword arguments. Opt-in
(default-off); enable via ``[tool.lanorme.named_args] enabled = true``.

Exceptions (skipped silently):
    - Dunder methods (__init__, __str__, __eq__, etc.)
    - Methods decorated ``@override`` (the base class fixes their signature)
    - Dependency-injection markers (``Depends()`` parameters)
    - Test files (filenames starting with ``test_``)
    - Lambda expressions
    - Functions suppressed with ``# noqa: KWARG-001``

Run:
    lanorme check . --check=named_args
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import is_flag_set
from lanorme.sources import UnparseableFile, iter_modules, locate, build_unparseable_notice

# Parameters that are implicit receiver, never counted.
SELF_CLS_NAMES = {"self", "cls"}


def _is_test_file(*, file_path: str) -> bool:
    """Return True if the file is a test file (name starts with ``test_``)."""
    return Path(file_path).name.startswith("test_")


def _is_dunder(*, name: str) -> bool:
    """Return True if *name* is a dunder (magic) method."""
    return name.startswith("__") and name.endswith("__")


# The modules whose ``override`` marks a method as overriding its base.
_OVERRIDE_MODULES = frozenset({"typing", "typing_extensions"})


def _is_override(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function is decorated ``@override`` / ``@typing.override``.

    An override must keep the signature its base class declares, so the bare
    ``*`` cannot be added here: the finding belongs on the base method.
    """
    for decorator in node.decorator_list:
        # ``typing.override`` is never called, so a call (Django's
        # ``@translation.override("fr")``) is some other decorator.
        if isinstance(decorator, ast.Name) and decorator.id == "override":
            return True
        if (
            isinstance(decorator, ast.Attribute)
            and decorator.attr == "override"
            and isinstance(decorator.value, ast.Name)
            and decorator.value.id in _OVERRIDE_MODULES
        ):
            return True
    return False


def _annotation_has_depends(*, annotation: ast.expr) -> bool:
    """Recursively check whether an annotation AST node contains a ``Depends(...)`` call."""
    for node in ast.walk(annotation):
        if isinstance(node, ast.Call):
            # Depends(...)
            if isinstance(node.func, ast.Name) and node.func.id == "Depends":
                return True
            # qualified <module>.Depends(...)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "Depends":
                return True
    return False


def _default_is_depends(*, default: ast.expr) -> bool:
    """Return True if a default value is a ``Depends(...)`` call."""
    if isinstance(default, ast.Call):
        if isinstance(default.func, ast.Name) and default.func.id == "Depends":
            return True
        if isinstance(default.func, ast.Attribute) and default.func.attr == "Depends":
            return True
    return False


def _count_real_params(
    *,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> int:
    """Count non-self/cls, non-Depends positional parameters.

    Parameters in ``node.args.args`` that are *not* self/cls and *not*
    framework-injected via ``Depends()`` are "real", the ones that callers
    supply.  ``kwonlyargs`` (already after ``*``) are not counted because
    they are already keyword-only.
    """
    # Positional defaults align to the tail of (posonlyargs + args), so both
    # must be included or the alignment below can go out of range.
    positional_args = node.args.posonlyargs + node.args.args

    # Build a list of defaults aligned to the end of positional_args.
    # Python AST stores defaults right-aligned: the last N args have defaults.
    num_defaults = len(node.args.defaults)
    defaults_padded: list[ast.expr | None] = [None] * (len(positional_args) - num_defaults) + list(
        node.args.defaults,
    )

    count = 0
    for arg, default in zip(positional_args, defaults_padded, strict=True):
        # Skip self / cls.
        if arg.arg in SELF_CLS_NAMES:
            continue

        # Positional-only params can never be passed by keyword, so the bare-*
        # rule cannot apply to them.
        if arg in node.args.posonlyargs:
            continue

        # Skip Depends() in annotation (Annotated[..., Depends(...)]).
        if arg.annotation and _annotation_has_depends(annotation=arg.annotation):
            continue

        # Skip Depends() as default value.
        if default and _default_is_depends(default=default):
            continue

        count += 1

    return count


def _has_bare_star(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function already enforces keyword-only args.

    A function is compliant when **all** non-self/cls, non-Depends
    positional parameters are zero, meaning every real parameter is already
    in ``kwonlyargs`` (after the bare ``*``).
    """
    return _count_real_params(node=node) == 0


def _line_has_noqa(*, source_lines: list[str], lineno: int) -> bool:
    """Return True if the function definition line contains ``# noqa: KWARG-001``."""
    # lineno is 1-based; also check continuation lines for decorators.
    if 1 <= lineno <= len(source_lines):
        line = source_lines[lineno - 1]
        if "# noqa: KWARG-001" in line:
            return True
    return False


def _check_function(
    *,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
    relative_file: str,
) -> Violation | None:
    """Check a single function node for KWARG-001 compliance."""
    # Skip dunder methods and overrides: their signature is fixed elsewhere.
    if _is_dunder(name=node.name) or _is_override(node=node):
        return None

    # Skip if suppressed.
    if _line_has_noqa(source_lines=source_lines, lineno=node.lineno):
        return None

    # Count how many real positional params exist (excluding self/cls & Depends).
    real_positional = _count_real_params(node=node)

    # If 0 or 1 real positional params, no bare * needed.
    if real_positional <= 1:
        return None

    # If the function already has bare *, all real params are keyword-only → compliant.
    # (This is implied by real_positional > 1, they are still in args, not kwonlyargs.)
    # So if we reach here, the function is non-compliant.
    return Violation(
        file=relative_file,
        line=node.lineno,
        rule="KWARG-001: Functions with >1 parameter must use bare * separator",
        message=f"Function '{node.name}' has {real_positional} positional params without bare *",
        fix="Add a bare * separator: def foo(self, *, param1: str, param2: int)",
        **locate(node),
    )


@dataclass
class NamedArgsCheck:
    """Enforces named arguments via bare ``*`` separator on multi-parameter functions."""

    name: str = "named_args"
    description: str = "Named arguments enforcement (bare * separator)"
    # KWARG-001 ships default-off: the audit flagged it as opinionated
    # house style that will flood any codebase that doesn't already
    # mandate keyword-only call sites. Opt in via
    # ``[tool.lanorme.named_args] enabled = true``.
    enabled: bool = False
    rules: list[str] = field(
        default_factory=lambda: [
            "KWARG-001: Functions with >1 parameter must use bare * separator (opt-in)",
        ],
    )
    settings_keys: ClassVar[frozenset[str]] = frozenset({"enabled"})

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.named_args]`` configuration."""
        self.enabled = is_flag_set(settings=settings, key="enabled", default=self.enabled)

    def run(self, *, src_root: str) -> CheckResult:
        """Scan all Python files under src/ and flag functions missing bare ``*``."""
        if not self.enabled:
            return CheckResult.from_findings(check=self.name)
        violations: list[Violation] = []
        warnings: list[Violation] = []

        for module in iter_modules(Path(src_root)):
            relative_file = module.relative

            # Skip test files entirely.
            if _is_test_file(file_path=relative_file):
                continue

            if isinstance(module, UnparseableFile):
                warnings.append(build_unparseable_notice(prefix="KWARG", failure=module))
                continue

            source_lines = module.lines

            for node in module.index.functions:
                violation = _check_function(
                    node=node,
                    source_lines=source_lines,
                    relative_file=relative_file,
                )
                if violation:
                    violations.append(violation)

        return CheckResult.from_findings(check=self.name, violations=violations, warnings=warnings)


# Self-register on import.
register(NamedArgsCheck())
