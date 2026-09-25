"""LAYER-001 through LAYER-005: Hexagonal layer dependency validation.

Uses Python's ast module to parse imports statically, no runtime execution.
Each file's layer is determined by its path under the source root. An import is
classified as a project layer when its first segment is a layer name
(``domain.models``) or the project's own top-level package followed by a layer
(``mypkg.domain.models``); the package name is taken from ``source_root``. A
third-party import whose submodule merely shares a layer name
(``thirdparty.infrastructure``) is therefore left alone.

Dependency rules (inward only), with the default layer set:
    domain/           -> nothing else in the source tree (pure Python + stdlib)
    application/      -> domain/ only
    infrastructure/   -> domain/ + application/ only
    api/              -> domain/ + application/ only
    composition root  -> EXCEPTION: a transport-layer file (api/ by default; see
                         transport_layers) matching a composition-root glob may
                         also import infrastructure/ (binds ports to adapters)

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

    A relative import is resolved against the importing file's package first,
    so ``from .application import X`` inside ``domain/`` names a sibling module
    in the domain layer, not the application layer.

    # For layouts whose hexagon differs. Defaults shown.
    layers  = ["domain", "application", "infrastructure", "api"]

    # Transport (inbound adapter) layers eligible for the composition-root
    # exception. Peers of api/ with identical import rules, e.g. an MCP or gRPC
    # server. A transport layer must also appear in ``layers`` and be given an
    # ``allowed`` entry.
    transport_layers = ["api"]
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
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import read_str_list, read_str
from lanorme.scan import Scan
from lanorme.sources import Module, UnparseableFile, iter_modules, locate, build_unparseable_notice

# The architectural layers in a hexagonal backend (default).
LAYERS = ("domain", "application", "infrastructure", "api")

# Transport (inbound adapter) layers eligible for the composition-root exception
# (default). A transport layer may host a composition root that binds ports to
# infrastructure adapters. Peers such as an MCP or gRPC server are added via
# [tool.lanorme.layer_deps] transport_layers.
TRANSPORT_LAYERS = ("api",)

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
    "api/dependencies.py",
    "api/deps.py",
    "api/v1/dependencies/**",
    "api/v1/dependencies.py",
    "api/v1/deps.py",
    "api/v1/main.py",
)

RULE_MAP = {
    "domain": "LAYER-001: domain/ must not import from any other layer (pure Python only)",
    "application": "LAYER-002: application/ can only import from domain/",
    "infrastructure": "LAYER-003: infrastructure/ can only import from domain/ and application/",
    "api": "LAYER-004: api/ can only import from domain/ and application/",
    "api_composition": "LAYER-005: only the composition root may import from infrastructure/",
    "custom": "LAYER-007: a configured layer may only import the layers its 'allowed' entry lists",
}

# Inner layers carry their own rules (LAYER-001..003). Any OTHER layer (api/ or a
# transport peer) importing infrastructure/ outside a composition root is the
# composition-root violation (LAYER-005), not the generic fallback.
_INNER_LAYERS = frozenset({"domain", "application", "infrastructure"})


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


_ImportNode = ast.Import | ast.ImportFrom


def _resolve_relative_module(*, node: ast.ImportFrom, classify_rel: str) -> str | None:
    """The layer-relative dotted module a relative import names, or ``None``.

    ``from .application import X`` in ``domain/model.py`` is
    ``domain.application``, a sibling inside the layer, not the application
    layer; ``from ..infrastructure import db`` there climbs out to
    ``infrastructure``. An import that climbs above the layer root has no
    layer-relative name and is left to the caller as ``None``.
    """
    package_parts = classify_rel.replace("\\", "/").split("/")[:-1]
    if node.level > len(package_parts):
        return node.module
    base = package_parts[: len(package_parts) - (node.level - 1)]
    parts = [*base, *(node.module.split(".") if node.module else [])]
    return ".".join(parts) if parts else None


def _extract_src_imports(
    *,
    module: Module,
    layers: tuple[str, ...],
    package: str,
    classify_rel: str,
) -> list[tuple[str, _ImportNode]]:
    """Extract imports that reference architectural layers, as (target_layer, import node)."""
    imports: list[tuple[str, _ImportNode]] = []
    for imported in module.imports:
        node = imported.node
        if not imported.is_from:
            target = imported.module
        elif imported.level:
            target = _resolve_relative_module(node=node, classify_rel=classify_rel)
        else:
            target = imported.module
        if target:
            _record_layer_import(
                module=target,
                node=node,
                imports=imports,
                layers=layers,
                package=package,
            )
    return imports


def _record_layer_import(
    *,
    module: str,
    node: _ImportNode,
    imports: list[tuple[str, _ImportNode]],
    layers: tuple[str, ...],
    package: str,
) -> None:
    """If a module path references one of the architectural layers, record it."""
    # Imports look like domain.models (bare layer) or mypkg.domain.models (the
    # wrapping package, then a layer). Only the project's own top-level package
    # -- the final component of source_root, passed in as *package* -- may
    # precede a layer. A first segment that is neither a layer nor that package
    # is third-party, so a name such as thirdparty.infrastructure is left alone.
    parts = module.split(".")
    if parts[0] in layers:
        target = parts[0]
    elif package and parts[0] == package and len(parts) > 1:
        target = parts[1]
    else:
        return
    if target in layers:
        imports.append((target, node))


def _suggest_fix(
    *,
    source_layer: str,
    target_layer: str,
    allowed_imports: dict[str, set[str]],
) -> str:
    """Generate a human-readable fix suggestion for a layer violation."""
    suggestions = {
        ("domain", "application"): "Domain must be pure: move the needed type to domain/",
        (
            "domain",
            "infrastructure",
        ): "Domain must be pure: define a port in application/ports/ instead",
        ("domain", "api"): "Domain must be pure: this dependency is inverted",
        (
            "application",
            "infrastructure",
        ): "Depend on a port (Protocol) in application/ports/, not the concrete implementation",
        (
            "application",
            "api",
        ): "Application must not know about the API layer: invert the dependency",
        (
            "api",
            "infrastructure",
        ): "Use dependency injection via the composition root instead of direct imports",
    }
    allowed = ", ".join(f"{name}/" for name in sorted(allowed_imports.get(source_layer, set())))
    return suggestions.get(
        (source_layer, target_layer),
        f"Remove the import from {target_layer}/; {source_layer}/ may import "
        + (f"only {allowed}" if allowed else "no other layer"),
    )


@dataclass
class LayerDepsCheck:
    """Validates hexagonal layer dependency rules (configurable layout)."""

    settings_keys: ClassVar[frozenset[str]] = frozenset(
        {"source_root", "composition_root", "layers", "transport_layers", "allowed"},
    )

    name: str = "layer_deps"
    description: str = "Hexagonal architecture layer dependency validation"
    scope = "tree"  # classifies layers by layout relative to the source root
    source_root: str = ""
    layers: tuple[str, ...] = LAYERS
    transport_layers: tuple[str, ...] = TRANSPORT_LAYERS
    allowed_imports: dict[str, set[str]] = field(
        default_factory=lambda: {layer: set(targets) for layer, targets in ALLOWED_IMPORTS.items()},
    )
    composition_root: tuple[str, ...] = COMPOSITION_ROOT_GLOBS
    rules: list[str] = field(
        default_factory=lambda: [
            "LAYER-001: domain/ must not import from any other layer (pure Python only)",
            "LAYER-002: application/ can only import from domain/",
            "LAYER-003: infrastructure/ can only import from domain/ and application/",
            "LAYER-004: api/ can only import from domain/ and application/",
            "LAYER-005: only the composition root may import from infrastructure/",
            "LAYER-006: a transport layer is not among the configured layers",
            "LAYER-007: a configured layer may only import the layers its 'allowed' entry lists",
        ],
    )

    # Not a dataclass field (no annotation): tracks whether the user explicitly
    # configured transport_layers, so the LAYER-006 advisory fires for real
    # intent only, never for the default ("api",) under a renamed layout.
    _transport_configured = False

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.layer_deps]`` configuration."""
        self.source_root = (
            read_str(settings=settings, key="source_root", default=self.source_root)
            .replace("\\", "/")
            .strip("/")
        )
        self.composition_root = read_str_list(
            settings=settings,
            key="composition_root",
            default=self.composition_root,
        )
        layers = read_str_list(settings=settings, key="layers", default=self.layers)
        if layers:
            self.layers = layers
        transport = read_str_list(settings=settings, key="transport_layers", default=())
        if transport:
            self.transport_layers = transport
            self._transport_configured = True
        allowed = settings.get("allowed")
        if allowed is not None and not isinstance(allowed, dict):
            raise TypeError(f"'allowed' must be a table, got {type(allowed).__name__}")
        if isinstance(allowed, dict):
            self.allowed_imports = {
                str(layer): {str(target) for target in targets}
                for layer, targets in allowed.items()
                if isinstance(targets, list)
            }

    def _resolve_allowed_for_file(self, *, relative: str, layer: str) -> set[str]:
        """Allowed import targets for a file, adding the composition-root exception."""
        allowed = set(self.allowed_imports.get(layer, set()))
        if layer in self.transport_layers and _matches_glob(
            relative=relative,
            patterns=self.composition_root,
        ):
            allowed.add("infrastructure")
        return allowed

    def _build_violation(
        self,
        *,
        layer: str,
        target_layer: str,
        relative: str,
        node: _ImportNode,
        is_comp_root: bool,
    ) -> Violation:
        if target_layer == "infrastructure" and layer not in _INNER_LAYERS and not is_comp_root:
            rule = RULE_MAP["api_composition"]
            fix = (
                "Move this import to the composition root, or depend on the port "
                "in application/ports/ instead"
            )
        else:
            rule = RULE_MAP.get(layer, RULE_MAP["custom"])
            fix = _suggest_fix(
                source_layer=layer,
                target_layer=target_layer,
                allowed_imports=self.allowed_imports,
            )
        return Violation(
            file=relative,
            line=node.lineno,
            rule=rule,
            message=f"{layer}/ imports from {target_layer}/",
            fix=fix,
            **locate(node),
        )

    def _collect_config_warnings(self) -> list[Violation]:
        """LAYER-006: advise when a configured transport layer is not a known layer.

        Fires only when the user set ``transport_layers`` explicitly, so the
        default ``("api",)`` never warns under a renamed (non-api) layout.
        """
        if not getattr(self, "_transport_configured", False):
            return []
        return [
            Violation(
                file="[tool.lanorme.layer_deps]",
                line=0,
                rule="LAYER-006: a transport layer is not among the configured layers",
                message=f"transport_layers lists '{layer}', which is not in layers, so it has no effect",
                fix=f"Add '{layer}' to [tool.lanorme.layer_deps] layers, or remove it from transport_layers",
            )
            for layer in self.transport_layers
            if layer not in self.layers
        ]

    def check(self, scan: Scan) -> CheckResult:
        """Scan all Python files under the source root and validate import directions."""
        violations: list[Violation] = []
        warnings: list[Violation] = self._collect_config_warnings()
        src_path = scan.root
        # The architectural root. Layer classification and composition-root
        # globs are anchored here; Violation paths stay anchored at src_path so
        # they line up with --exclude / per-file-ignores / inline noqa comments.
        base = src_path / self.source_root if self.source_root else src_path
        # The project's own top-level package: the final component of
        # source_root (mypkg for src/myapp). Empty when source_root is unset, in
        # which case only a bare layer name (domain.models) is a project import.
        package = self.source_root.rsplit("/", 1)[-1] if self.source_root else ""

        for module in iter_modules(src_path):
            relative = module.relative
            try:
                classify_rel = module.path.relative_to(base).as_posix()
            except ValueError:
                continue  # outside the source root → layer-exempt
            layer = _classify_layer(relative=classify_rel, layers=self.layers)
            if layer is None:
                continue
            if isinstance(module, UnparseableFile):
                warnings.append(build_unparseable_notice(prefix="LAYER", failure=module))
                continue

            imports = _extract_src_imports(
                module=module,
                layers=self.layers,
                package=package,
                classify_rel=classify_rel,
            )
            allowed = self._resolve_allowed_for_file(relative=classify_rel, layer=layer)
            # A composition root only counts inside a transport layer, so a file
            # matching a glob in another layer is not silently treated as exempt.
            is_comp_root = layer in self.transport_layers and _matches_glob(
                relative=classify_rel,
                patterns=self.composition_root,
            )

            for target_layer, node in imports:
                if target_layer == layer or target_layer in allowed:
                    continue
                violations.append(
                    self._build_violation(
                        layer=layer,
                        target_layer=target_layer,
                        relative=relative,
                        node=node,
                        is_comp_root=is_comp_root,
                    ),
                )

        return CheckResult.from_findings(check=self.name, violations=violations, warnings=warnings)


# Self-register on import.
register(LayerDepsCheck())
