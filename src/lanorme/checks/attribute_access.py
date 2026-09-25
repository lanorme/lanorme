"""ATTR-001 and ATTR-002: low-level attribute-access smells.

``getattr`` / ``setattr`` / ``hasattr`` / ``delattr`` with a *constant*
attribute name are usually a sign of a missing type. If the attribute name is
known at the call site, the type is known too, so the dynamic form only blinds
the type checker for no benefit.

    ATTR-001  ``hasattr(x, "name")`` branches on structure (duck typing).
              Prefer a ``runtime_checkable`` ``Protocol`` with ``isinstance``,
              or EAFP (``try: ... except AttributeError``).
    ATTR-002  ``getattr(x, "name")`` (no default), ``setattr(x, "name", v)``,
              or ``delattr(x, "name")`` with a constant name. Use direct
              attribute access (``x.name``).

Both are advisory (WARNING) and opt-in (the check ships default-off). Enable
via ``[tool.lanorme.attribute_access] enabled = true``. The high-confidence
cases only:

    - The attribute name must be a string literal that is a valid identifier.
      A non-identifier name (``getattr(x, "weird-key")``) cannot be written as
      ``x.attr`` and is left alone.
    - Dunder names (``__class__``, ``__name__`` ...) are introspection, exempt.
    - Three-argument ``getattr(x, "name", default)`` is the legitimate
      safe-access idiom, exempt.
    - Files under ``tests/`` are exempt (tests poke internals on purpose).

Dynamic names (``getattr(x, name)``, ``getattr(x, "_" + n)``) are genuine
reflection and exempt by default. Enable ``flag_dynamic`` to flag them too::

    [tool.lanorme.attribute_access]
    enabled = true
    flag_dynamic = false   # also flag non-literal attribute names

Run:
    lanorme check . --check=attribute_access
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import is_flag_set
from lanorme.sources import iter_parsed_modules, locate

_ATTR_BUILTINS = frozenset({"getattr", "hasattr", "setattr", "delattr"})

# Files under these path fragments are skipped (intentional internal poking).
_EXEMPT_PATH_FRAGMENTS = ("tests/", "test/")


def _is_exempt_file(*, relative: str) -> bool:
    norm = relative.replace("\\", "/")
    if Path(norm).name.startswith("test_"):
        return True
    return any(norm.startswith(p) or f"/{p}" in norm for p in _EXEMPT_PATH_FRAGMENTS)


def _extract_builtin_name(*, call: ast.Call) -> str | None:
    """Return the builtin name if *call* is a bare getattr/hasattr/setattr/delattr."""
    func = call.func
    if isinstance(func, ast.Name) and func.id in _ATTR_BUILTINS:
        return func.id
    return None


def _extract_literal_name(*, node: ast.AST) -> str | None:
    """Return the string value if *node* is a string-literal attribute name."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _check_attr001(*, builtin: str, name: str, relative: str, call: ast.Call) -> Violation:
    return Violation(
        file=relative,
        line=call.lineno,
        rule="ATTR-001: Avoid hasattr() for type discrimination",
        message=f"hasattr(..., '{name}') branches on structure (duck typing)",
        fix=(
            "Model the expected shape as a runtime_checkable Protocol and use "
            "isinstance, or use try/except AttributeError (EAFP)"
        ),
        **locate(call),
    )


def _check_attr002(*, builtin: str, name: str, relative: str, call: ast.Call) -> Violation:
    access = {
        "getattr": f"obj.{name}",
        "setattr": f"obj.{name} = value",
        "delattr": f"del obj.{name}",
    }[builtin]
    return Violation(
        file=relative,
        line=call.lineno,
        rule="ATTR-002: Avoid getattr/setattr/delattr with a literal attribute name",
        message=f"{builtin}(..., '{name}') with a constant name defeats static typing",
        fix=f"Use direct attribute access ({access})",
        **locate(call),
    )


@dataclass
class AttributeAccessCheck:
    """Flags low-level attribute access that usually signals a missing type."""

    name: str = "attribute_access"
    description: str = "Low-level getattr/hasattr/setattr/delattr smells"
    enabled: bool = False
    flag_dynamic: bool = False
    rules: list[str] = field(
        default_factory=lambda: [
            "ATTR-001: Avoid hasattr() for type discrimination",
            "ATTR-002: Avoid getattr/setattr/delattr with a literal attribute name",
        ],
    )
    settings_keys: ClassVar[frozenset[str]] = frozenset({"enabled", "flag_dynamic"})

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.attribute_access]`` configuration."""
        self.enabled = is_flag_set(settings=settings, key="enabled", default=self.enabled)
        self.flag_dynamic = is_flag_set(
            settings=settings,
            key="flag_dynamic",
            default=self.flag_dynamic,
        )

    def _call_warning(self, *, call: ast.Call, relative: str) -> Violation | None:
        builtin = _extract_builtin_name(call=call)
        if builtin is None or len(call.args) < 2:
            return None
        # Three-arg getattr(x, name, default) is the safe-access idiom.
        if builtin == "getattr" and len(call.args) >= 3:
            return None

        name = _extract_literal_name(node=call.args[1])
        if name is None:
            return self._build_dynamic_warning(builtin=builtin, call=call, relative=relative)
        if not name.isidentifier() or _is_dunder(name):
            return None
        if builtin == "hasattr":
            return _check_attr001(builtin=builtin, name=name, relative=relative, call=call)
        return _check_attr002(builtin=builtin, name=name, relative=relative, call=call)

    def _build_dynamic_warning(
        self,
        *,
        builtin: str,
        call: ast.Call,
        relative: str,
    ) -> Violation | None:
        """Flag a non-literal attribute name only when flag_dynamic is enabled."""
        if not self.flag_dynamic:
            return None
        rule = (
            "ATTR-001: Avoid hasattr() for type discrimination"
            if builtin == "hasattr"
            else "ATTR-002: Avoid getattr/setattr/delattr with a literal attribute name"
        )
        return Violation(
            file=relative,
            line=call.lineno,
            rule=rule,
            message=f"{builtin}(...) with a dynamic attribute name (reflection)",
            fix="Prefer a typed object or Protocol over reflective attribute access",
            **locate(call),
        )

    def run(self, *, src_root: str) -> CheckResult:
        if not self.enabled:
            return CheckResult.from_findings(check=self.name)

        warnings: list[Violation] = []
        for module in iter_parsed_modules(Path(src_root)):
            if _is_exempt_file(relative=module.relative):
                continue
            for node in module.index.collect(ast.Call):
                warning = self._call_warning(call=node, relative=module.relative)
                if warning is not None:
                    warnings.append(warning)

        return CheckResult.from_findings(check=self.name, warnings=warnings)


# Self-register on import.
register(AttributeAccessCheck())
