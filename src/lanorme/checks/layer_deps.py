"""LAYER-001 through LAYER-005: Hexagonal layer dependency validation.

Uses Python's ast module to parse imports statically, no runtime execution.
Each file's layer is determined by its path under the source root. A file's
top-level package prefix (``mypkg.domain.models``) is stripped before the layer
is classified, so the check is package-name agnostic.

Dependency rules (inward only), with the default layer set:
    domain/           -> nothing else in the source tree (pure Python + stdlib)
    application/      -> domain/ only
    infrastructure/   -> domain/ + application/ only
    api/              -> domain/ + application/ only
    composition root  -> EXCEPTION: api files matching a composition-root glob
                         may also import infrastructure/ (binds ports to adapters)

Projects that do not use these layer directories produce no findings: the
check is naturally inert outside a layered layout.

If the architectural layers live under a nested package directory, set the
top-level ``[tool.lanorme] source_root`` so layers are classified relative to
it (``source_root/domain/``, ``source_root/api/`` ...). Files outside
``source_root`` are layer-exempt. ``composition_root`` is then interpreted
relative to ``source_root`` too.

Configure it in ``[tool.lanorme.layer_deps]`` (all keys optional; the defaults
are shown):

    [tool.lanorme.layer_deps]
    # Files allowed to import the infrastructure layer (the composition root).
    # Glob-matched (fnmatch) against the source-root-relative path, so a module
    # FILE such as api/dependencies.py is recognised, not only a directory.
    composition_root = ["api/dependencies.py", "api/app.py"]

    # For layouts whose hexagon differs. Defaults shown.
    layers  = ["domain", "application", "infrastructure", "api"]
    [tool.lanorme.layer_deps.allowed]
    application    = ["domain"]
    infrastructure = ["domain", "application"]
    api            = ["domain", "application"]

Run:
    lanorme check . --check=layer_deps
"""

from __future__ import annotations

import ast
import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.discovery import iter_py_files

# The architectural layers in a hexagonal backend (default).
LAYERS = ("domain", "application", "infrastructure", "api")

# What each layer is ALLOWED to import from within the source tree (default).
# Empty set = no inter-layer imports allowed.
ALLOWED_IMPORTS: dict[str, set[str]] = {
    "domain": set(),
    "application": {"domain"},
    "infrastructure": {"domain", "application"},
    "api": {"domain", "application"},
}

# Composition-root exception (default): files matching these globs (DI wiring
# and the application factory) may import from infrastructure/ to bind ports to
# adapters. Glob-matched so both directories and single module files work.
COMPOSITION_ROOT_GLOBS = (
    "api/dependencies/**",
    "api/v1/dependencies/**",
    "api/v1/main.py",
)

RULE_MAP = {
    "domain": "LAYER-001: domain/ must not import from any other layer (pure Python only)",
    "application": "LAYER-002: application/ can only import from domain/",
    "infrastructure": "LAYER-003: infrastructure/ can only import from domain/ and application/",
    "api": "LAYER-004: api/ can only import from domain/ and application/",
    "api_composition": "LAYER-005: only the composition root may import from infrastructure/",
}


def _matches_glob(*, relative: str, patterns: tuple[str, ...]) -> bool:
    """True if the forward-slash relative path matches any fnmatch glob."""
    rel = relative.replace("\\", "/")
    return any(fnmatch.fnmatch(rel, pattern) for pattern in patterns)


def _classify_layer(*, relative: str, layers: tuple[str, ...]) -> str | None:
    """Determine which architectural layer a relative path belongs to."""
    rel = relative.replace("\\", "/")
    for layer in layers:
        if rel.startswith(f"{layer}/"):
            return layer
    return None


def _extract_src_imports(*, tree: ast.AST, layers: tuple[str, ...]) -> list[tuple[str, int]]:
    """Extract imports that reference architectural layers, as (target_layer, line)."""
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _record_layer_import(module=alias.name, line=node.lineno, imports=imports, layers=layers)
        elif isinstance(node, ast.ImportFrom) and node.module:
            _record_layer_import(module=node.module, line=node.lineno, imports=imports, layers=layers)
    return imports


def _record_layer_import(
    *,
    module: str,
    line: int,
    imports: list[tuple[str, int]],
    layers: tuple[str, ...],
) -> None:
    """If a module path references one of the architectural layers, record it."""
    # Imports look like mypkg.domain.models or domain.models. Strip an optional
    # top-level package prefix (anything that is not itself a layer name) before
    # classifying, so the check does not depend on the package's name.
    parts = module.split(".")
    target = parts[1] if parts[0] not in layers and len(parts) > 1 else parts[0]
    if target in layers:
        imports.append((target, line))


def _suggest_fix(
    *,
    source_layer: str,
    target_layer: str,
    allowed_imports: dict[str, set[str]],
) -> str:
    """Generate a human-readable fix suggestion for a layer violation."""
    suggestions = {
        ("domain", "application"): "Domain must be pure: move the needed type to domain/",
        ("domain", "infrastructure"): "Domain must be pure: define a port in application/ports/ instead",
        ("domain", "api"): "Domain must be pure: this dependency is inverted",
        ("application", "infrastructure"): "Depend on a port (Protocol) in application/ports/, not the concrete implementation",
        ("application", "api"): "Application must not know about the API layer: invert the dependency",
        ("api", "infrastructure"): "Use dependency injection via the composition root instead of direct imports",
    }
    return suggestions.get(
        (source_layer, target_layer),
        f"Remove the import from {target_layer}/: only allowed: {', '.join(sorted(allowed_imports.get(source_layer, set())))}",
    )


@dataclass
class LayerDepsCheck:
    """Validates hexagonal layer dependency rules (configurable layout)."""

    name: str = "layer_deps"
    description: str = "Hexagonal architecture layer dependency validation"
    source_root: str = ""
    layers: tuple[str, ...] = LAYERS
    allowed_imports: dict[str, set[str]] = field(
        default_factory=lambda: {layer: set(targets) for layer, targets in ALLOWED_IMPORTS.items()}
    )
    composition_root: tuple[str, ...] = COMPOSITION_ROOT_GLOBS
    rules: list[str] = field(
        default_factory=lambda: [
            "LAYER-001: domain/ must not import from any other layer (pure Python only)",
            "LAYER-002: application/ can only import from domain/",
            "LAYER-003: infrastructure/ can only import from domain/ and application/",
            "LAYER-004: api/ can only import from domain/ and application/",
            "LAYER-005: only the composition root may import from infrastructure/",
        ]
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.layer_deps]`` configuration."""
        source_root = settings.get("source_root")
        if isinstance(source_root, str):
            self.source_root = source_root.replace("\\", "/").strip("/")
        comp = settings.get("composition_root")
        if isinstance(comp, list):
            self.composition_root = tuple(str(pattern) for pattern in comp)
        layers = settings.get("layers")
        if isinstance(layers, list) and layers:
            self.layers = tuple(str(layer) for layer in layers)
        allowed = settings.get("allowed")
        if isinstance(allowed, dict):
            self.allowed_imports = {
                str(layer): {str(target) for target in targets}
                for layer, targets in allowed.items()
                if isinstance(targets, list)
            }

    def _allowed_for_file(self, *, relative: str, layer: str) -> set[str]:
        """Allowed import targets for a file, adding the composition-root exception."""
        allowed = set(self.allowed_imports.get(layer, set()))
        if layer == "api" and _matches_glob(relative=relative, patterns=self.composition_root):
            allowed.add("infrastructure")
        return allowed

    def _violation_for(
        self, *, layer: str, target_layer: str, relative: str, line: int, is_comp_root: bool
    ) -> Violation:
        if layer == "api" and target_layer == "infrastructure" and not is_comp_root:
            rule = RULE_MAP["api_composition"]
            fix = (
                "Move this import to the composition root, or depend on the port "
                "in application/ports/ instead"
            )
        else:
            rule = RULE_MAP.get(layer, f"LAYER: {layer}/ cannot import {target_layer}/")
            fix = _suggest_fix(source_layer=layer, target_layer=target_layer, allowed_imports=self.allowed_imports)
        return Violation(
            file=relative,
            line=line,
            rule=rule,
            message=f"{layer}/ imports from {target_layer}/",
            fix=fix,
        )

    def run(self, *, src_root: str) -> CheckResult:
        """Scan all Python files under the source root and validate import directions."""
        violations: list[Violation] = []
        warnings: list[Violation] = []
        src_path = Path(src_root)
        # The architectural root. Layer classification and composition-root
        # globs are anchored here; Violation paths stay anchored at src_path so
        # they line up with --exclude / per-file-ignores / # noqa.
        base = src_path / self.source_root if self.source_root else src_path

        for py_file in iter_py_files(src_path):
            relative = py_file.relative_to(src_path).as_posix()
            try:
                classify_rel = py_file.relative_to(base).as_posix()
            except ValueError:
                continue  # outside the source root → layer-exempt
            layer = _classify_layer(relative=classify_rel, layers=self.layers)
            if layer is None:
                continue

            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
            except (OSError, UnicodeDecodeError, SyntaxError):
                warnings.append(
                    Violation(
                        file=relative,
                        line=0,
                        rule="LAYER-000: parse error",
                        message=f"Could not parse {py_file.name} — skipping",
                        fix="Fix the syntax error first",
                    )
                )
                continue

            imports = _extract_src_imports(tree=tree, layers=self.layers)
            allowed = self._allowed_for_file(relative=classify_rel, layer=layer)
            is_comp_root = _matches_glob(relative=classify_rel, patterns=self.composition_root)

            for target_layer, line in imports:
                if target_layer == layer or target_layer in allowed:
                    continue
                violations.append(
                    self._violation_for(
                        layer=layer,
                        target_layer=target_layer,
                        relative=relative,
                        line=line,
                        is_comp_root=is_comp_root,
                    )
                )

        status = Status.FAIL if violations else (Status.WARN if warnings else Status.PASS)
        return CheckResult(
            check=self.name,
            status=status,
            violations=violations,
            warnings=warnings,
        )


# Self-register on import.
register(LayerDepsCheck())
