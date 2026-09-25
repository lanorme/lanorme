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
    - ``hasattr`` on a receiver bound by a plain ``import`` (``hasattr(os,
      "fork")``, ``hasattr(socket, "AF_UNIX")``) is platform feature
      detection on a module, not duck typing of an object, exempt, and so is
      ``getattr`` on one with a literal name. A ``setattr`` / ``delattr`` on
      a module, or a ``getattr`` through one by a computed name, is not
      detection and is still reported.
    - Test files (see ``lanorme.paths``) are exempt (tests poke internals
      on purpose).

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
from lanorme.paths import is_test_file
from lanorme.sources import Module, iter_parsed_modules, locate

_ATTR_BUILTINS = frozenset({"getattr", "hasattr", "setattr", "delattr"})


def _is_exempt_file(*, relative: str) -> bool:
    """True for test files (see ``lanorme.paths``): tests poke internals on purpose."""
    return is_test_file(relative)


def _collect_imported_module_names(*, module: Module) -> frozenset[str]:
    """The local names a plain ``import x`` / ``import x.y as z`` binds to a module."""
    names: set[str] = set()
    for node in module.index.collect(ast.Import):
        for alias in node.names:
            names.add(alias.asname or alias.name.split(".")[0])
    return frozenset(names)


def _is_module_probe(*, call: ast.Call, builtin: str, module_names: frozenset[str]) -> bool:
    """True for feature detection on a module the file imported.

    ``hasattr(os, "fork")``, or ``getattr(sys, "getwindowsversion")`` with a
    literal name. Writing to a module (``setattr(settings, "DEBUG", True)``)
    or dispatching through one by a computed name (``getattr(handlers,
    action)()``) is not detection, and keeps its finding.
    """
    receiver = call.args[0]
    if not isinstance(receiver, ast.Name) or receiver.id not in module_names:
        return False
    if builtin == "hasattr":
        return True
    return builtin == "getattr" and _extract_literal_name(node=call.args[1]) is not None


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

    def _call_warning(
        self,
        *,
        call: ast.Call,
        relative: str,
        module_names: frozenset[str],
    ) -> Violation | None:
        builtin = _extract_builtin_name(call=call)
        if builtin is None or len(call.args) < 2:
            return None
        # Three-arg getattr(x, name, default) is the safe-access idiom.
        if builtin == "getattr" and len(call.args) >= 3:
            return None
        # Probing an imported module (hasattr(os, "fork")) is feature detection.
        if _is_module_probe(call=call, builtin=builtin, module_names=module_names):
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
            module_names = _collect_imported_module_names(module=module)
            for node in module.index.collect(ast.Call):
                warning = self._call_warning(
                    call=node,
                    relative=module.relative,
                    module_names=module_names,
                )
                if warning is not None:
                    warnings.append(warning)

        return CheckResult.from_findings(check=self.name, warnings=warnings)


# Self-register on import.
register(AttributeAccessCheck())
