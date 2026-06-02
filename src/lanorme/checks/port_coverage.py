"""PORT-001 through PORT-003: Port coverage enforcement for hexagonal architecture.

Verifies that infrastructure adapters implement port Protocols, that every
Protocol has at least one implementation, and that the API layer does not
directly import or instantiate infrastructure adapter classes.

Rules:
    PORT-001  Every non-utility adapter file (under the adapter roots) must
              import from the ports directory (structural subtyping link)
    PORT-002  Every Protocol in the ports directory (excluding the
              ``ports_without_impl`` files) must be referenced by at least one
              adapter file
    PORT-003  No direct import/instantiation of infrastructure adapter classes
              in the api/ layer outside the composition root

Configure it in ``[tool.lanorme.port_coverage]`` (all keys optional; the
defaults reproduce today's behaviour):

    [tool.lanorme.port_coverage]
    ports_dir        = "application/ports"     # where port Protocols live
    adapter_roots    = ["infrastructure"]      # dirs scanned for adapters (recursive)
    composition_root = ["api/dependencies.py", "api/app.py"]  # PORT-003 exemption (globs)
    skip_files       = ["__init__.py"]         # adapter files that are not adapters
    ports_without_impl = ["repositories.py", "unit_of_work.py"]  # ports backed elsewhere

Run:
    lanorme check . --check=port_coverage
"""

from __future__ import annotations

import ast
import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register

# Adapter files that are pure utilities or re-exports, not port implementations.
INFRA_SERVICE_SKIP_FILES = ("__init__.py",)

# Port files whose Protocols are implemented outside the adapter roots
# (repositories, unit-of-work, observability), so PORT-002 should not expect a
# matching adapter import for them.
PORT_FILES_WITHOUT_SERVICE_IMPL = (
    "repositories.py",
    "unit_of_work.py",
    "otel.py",
    "metrics.py",
)

# Default adapter roots and ports directory.
DEFAULT_ADAPTER_ROOTS = ("infrastructure/services",)
DEFAULT_PORTS_DIR = "application/ports"

# Default composition-root globs for PORT-003. The glob equivalents of the
# previous substring patterns ("dependencies/", "v1/main.py"); fnmatch is a
# full-path match, so the leading/trailing ``*`` reproduce the old behaviour
# and let a module file (api/dependencies.py) be added explicitly.
DEFAULT_COMPOSITION_ROOT = ("*dependencies/*", "*v1/main.py")


# ---- AST helpers -----------------------------------------------------------


def _matches_glob(*, relative: str, patterns: tuple[str, ...]) -> bool:
    rel = relative.replace("\\", "/")
    return any(fnmatch.fnmatch(rel, pattern) for pattern in patterns)


def _extract_protocol_names(*, tree: ast.AST) -> list[tuple[str, int]]:
    """Find all ``class Foo(Protocol): ...`` definitions, as (name, line)."""
    protocols: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            base_name: str | None = None
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr
            if base_name == "Protocol":
                protocols.append((node.name, node.lineno))
                break
    return protocols


def _extract_import_modules(*, tree: ast.AST) -> list[tuple[str, int]]:
    """Extract import source modules as (dotted_module, line) pairs."""
    modules: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append((node.module, node.lineno))
    return modules


def _extract_imported_names(*, tree: ast.AST) -> set[str]:
    """Collect all names brought into scope via import statements."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname if alias.asname else alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names or []:
                names.add(alias.asname if alias.asname else alias.name)
    return names


def _extract_call_names(*, tree: ast.AST) -> list[tuple[str, int]]:
    """Find ``SomeClass(...)`` call expressions and return (name, line)."""
    calls: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.append((node.func.id, node.lineno))
            elif isinstance(node.func, ast.Attribute):
                calls.append((node.func.attr, node.lineno))
    return calls


def _extract_top_level_class_names(*, tree: ast.AST) -> list[str]:
    """Get top-level (non-nested) class names from an AST."""
    return [node.name for node in ast.iter_child_nodes(tree) if isinstance(node, ast.ClassDef)]


def _imports_from_ports(*, import_modules: list[tuple[str, int]], ports_dotted: str) -> bool:
    """True if any import comes from the ports package (``application.ports`` by default)."""
    return any(ports_dotted in module for module, _line in import_modules)


def _port_module_stem(*, module: str, ports_parts: list[str]) -> str | None:
    """Return the port module filename stem from a dotted import path, or None.

    With ``ports_parts == ["application", "ports"]``:
    ``src.application.ports.registry`` -> ``registry``.
    """
    parts = module.split(".")
    span = len(ports_parts)
    for i in range(len(parts) - span + 1):
        if parts[i : i + span] == ports_parts:
            return parts[i + span] if i + span < len(parts) else None
    return None


def _parse_file(*, py_file: Path) -> ast.AST | None:
    try:
        source = py_file.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(py_file))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None


# ---- Scanning --------------------------------------------------------------


def _collect_port_protocols(
    *,
    src_path: Path,
    ports_dir: str,
    ports_without_impl: frozenset[str],
) -> dict[str, tuple[str, int, str]]:
    """Scan the ports directory and return {ProtocolName: (relative_file, line, stem)}."""
    protocols: dict[str, tuple[str, int, str]] = {}
    ports_path = src_path / ports_dir
    if not ports_path.is_dir():
        return protocols

    for py_file in sorted(ports_path.glob("*.py")):
        if py_file.name == "__init__.py" or py_file.name in ports_without_impl:
            continue
        tree = _parse_file(py_file=py_file)
        if tree is None:
            continue
        relative = str(py_file.relative_to(src_path))
        for name, line in _extract_protocol_names(tree=tree):
            protocols[name] = (relative, line, py_file.stem)
    return protocols


def _scan_adapter_files(
    *,
    src_path: Path,
    adapter_roots: tuple[str, ...],
    skip_files: frozenset[str],
) -> list[tuple[str, ast.AST, list[tuple[str, int]]]]:
    """Scan the adapter roots recursively and return (relative_file, tree, imports)."""
    results: list[tuple[str, ast.AST, list[tuple[str, int]]]] = []
    seen: set[Path] = set()
    for root in adapter_roots:
        root_path = src_path / root
        if not root_path.is_dir():
            continue
        for py_file in sorted(root_path.rglob("*.py")):
            if py_file in seen or py_file.name in skip_files:
                continue
            seen.add(py_file)
            tree = _parse_file(py_file=py_file)
            if tree is None:
                continue
            relative = str(py_file.relative_to(src_path))
            results.append((relative, tree, _extract_import_modules(tree=tree)))
    return results


# ---- Rule implementations -------------------------------------------------


def _check_port001(
    *,
    adapter_files: list[tuple[str, ast.AST, list[tuple[str, int]]]],
    ports_dotted: str,
    ports_dir: str,
) -> list[Violation]:
    """PORT-001: every adapter file must import from the ports directory."""
    violations: list[Violation] = []
    for relative_file, _tree, import_modules in adapter_files:
        if _imports_from_ports(import_modules=import_modules, ports_dotted=ports_dotted):
            continue
        violations.append(
            Violation(
                file=relative_file,
                line=1,
                rule="PORT-001: Infrastructure service must implement a port Protocol",
                message=f"File '{relative_file}' has no imports from {ports_dir}/",
                fix=(
                    f"Import and implement the corresponding Protocol from {ports_dir}/, "
                    "or add the file to skip_files if it is a pure utility"
                ),
            )
        )
    return violations


def _check_port002(
    *,
    port_protocols: dict[str, tuple[str, int, str]],
    adapter_files: list[tuple[str, ast.AST, list[tuple[str, int]]]],
    ports_parts: list[str],
) -> list[Violation]:
    """PORT-002: every port Protocol must be referenced by an adapter file."""
    referenced_port_stems: set[str] = set()
    for _relative, _tree, import_modules in adapter_files:
        for module, _line in import_modules:
            stem = _port_module_stem(module=module, ports_parts=ports_parts)
            if stem is not None:
                referenced_port_stems.add(stem)

    violations: list[Violation] = []
    for proto_name, (relative_file, line, port_stem) in sorted(port_protocols.items()):
        if port_stem in referenced_port_stems:
            continue
        violations.append(
            Violation(
                file=relative_file,
                line=line,
                rule="PORT-002: Port Protocol has no infrastructure implementation",
                message=(
                    f"Protocol '{proto_name}' (in {port_stem}.py) "
                    "is not imported by any adapter file"
                ),
                fix=(
                    "Create an adapter that imports from this port module, "
                    "or add the port file to ports_without_impl"
                ),
            )
        )
    return violations


def _direct_import_violation(
    *,
    relative: str,
    import_modules: list[tuple[str, int]],
    imported_infra: set[str],
    adapter_dotted: tuple[str, ...],
) -> Violation | None:
    """Report an api/ file importing adapter classes outside the composition root."""
    for module, line in import_modules:
        if any(dotted in module for dotted in adapter_dotted):
            return Violation(
                file=relative,
                line=line,
                rule="PORT-003: Direct import of infra service in api/ layer",
                message=(
                    f"Imports infrastructure adapter class(es) "
                    f"{sorted(imported_infra)} outside composition root"
                ),
                fix=(
                    "Depend on the port Protocol from the ports directory instead, "
                    "or move the import to the composition root"
                ),
            )
    return None


def _check_port003(
    *,
    src_path: Path,
    infra_class_names: set[str],
    adapter_dotted: tuple[str, ...],
    composition_root: tuple[str, ...],
) -> list[Violation]:
    """PORT-003: no direct import/instantiation of adapter classes in the api/ layer."""
    violations: list[Violation] = []
    api_dir = src_path / "api"
    if not api_dir.is_dir():
        return violations

    for py_file in sorted(api_dir.rglob("*.py")):
        relative = str(py_file.relative_to(src_path)).replace("\\", "/")
        if _matches_glob(relative=relative, patterns=composition_root):
            continue

        tree = _parse_file(py_file=py_file)
        if tree is None:
            continue

        import_modules = _extract_import_modules(tree=tree)
        if not any(dotted in module for module, _line in import_modules for dotted in adapter_dotted):
            continue

        imported_infra = _extract_imported_names(tree=tree) & infra_class_names
        if not imported_infra:
            continue

        found_instantiation = False
        for call_name, call_line in _extract_call_names(tree=tree):
            if call_name in imported_infra:
                violations.append(
                    Violation(
                        file=relative,
                        line=call_line,
                        rule="PORT-003: Direct instantiation of infra service in api/ layer",
                        message=f"'{call_name}(...)' instantiated directly — use dependency injection",
                        fix="Move the construction to the composition root and inject it",
                    )
                )
                found_instantiation = True

        if not found_instantiation:
            import_violation = _direct_import_violation(
                relative=relative,
                import_modules=import_modules,
                imported_infra=imported_infra,
                adapter_dotted=adapter_dotted,
            )
            if import_violation is not None:
                violations.append(import_violation)

    return violations


# ---- Check class -----------------------------------------------------------


@dataclass
class PortCoverageCheck:
    """Validates port/adapter coverage in the hexagonal architecture (configurable)."""

    name: str = "port_coverage"
    description: str = "Port coverage enforcement (Protocol / infrastructure alignment)"
    ports_dir: str = DEFAULT_PORTS_DIR
    adapter_roots: tuple[str, ...] = DEFAULT_ADAPTER_ROOTS
    composition_root: tuple[str, ...] = DEFAULT_COMPOSITION_ROOT
    skip_files: frozenset[str] = field(default_factory=lambda: frozenset(INFRA_SERVICE_SKIP_FILES))
    ports_without_impl: frozenset[str] = field(
        default_factory=lambda: frozenset(PORT_FILES_WITHOUT_SERVICE_IMPL)
    )
    rules: list[str] = field(
        default_factory=lambda: [
            "PORT-001: Every infrastructure service file must import from application/ports/",
            "PORT-002: Every port Protocol must have at least one infrastructure implementation",
            "PORT-003: No direct import/instantiation of infrastructure services in api/ layer",
        ],
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.port_coverage]`` configuration. Defaults reproduce today's behaviour."""
        ports_dir = settings.get("ports_dir")
        if isinstance(ports_dir, str) and ports_dir:
            self.ports_dir = ports_dir.replace("\\", "/").strip("/")
        for key in ("adapter_roots", "composition_root"):
            value = settings.get(key)
            if isinstance(value, list) and value:
                setattr(self, key, tuple(str(item) for item in value))
        for key in ("skip_files", "ports_without_impl"):
            value = settings.get(key)
            if isinstance(value, list):
                setattr(self, key, frozenset(str(item) for item in value))

    def run(self, *, src_root: str) -> CheckResult:
        """Scan ports and adapters and validate coverage."""
        violations: list[Violation] = []
        src_path = Path(src_root)

        ports_parts = self.ports_dir.split("/")
        ports_dotted = ".".join(ports_parts)
        adapter_dotted = tuple(root.replace("/", ".") for root in self.adapter_roots)

        port_protocols = _collect_port_protocols(
            src_path=src_path, ports_dir=self.ports_dir, ports_without_impl=self.ports_without_impl
        )
        adapter_files = _scan_adapter_files(
            src_path=src_path, adapter_roots=self.adapter_roots, skip_files=self.skip_files
        )

        violations.extend(
            _check_port001(adapter_files=adapter_files, ports_dotted=ports_dotted, ports_dir=self.ports_dir)
        )
        violations.extend(
            _check_port002(
                port_protocols=port_protocols, adapter_files=adapter_files, ports_parts=ports_parts
            )
        )

        infra_class_names: set[str] = set()
        for _relative, tree, _imports in adapter_files:
            infra_class_names.update(_extract_top_level_class_names(tree=tree))

        violations.extend(
            _check_port003(
                src_path=src_path,
                infra_class_names=infra_class_names,
                adapter_dotted=adapter_dotted,
                composition_root=self.composition_root,
            )
        )

        status = Status.FAIL if violations else Status.PASS
        return CheckResult(check=self.name, status=status, violations=violations)


# Self-register on import.
register(PortCoverageCheck())
