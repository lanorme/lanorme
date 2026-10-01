"""SHALLOW-001: a small package split into many shallow modules.

Checks:
    SHALLOW-001  A leaf package whose modules are mostly tiny, and which would fit
                 in one module well under SIZE-001's warning, is reported once,
                 on line 1 of its ``__init__.py``, with the module to merge into.

SIZE-001 and SIZE-002 push code apart when a file or a function grows too big.
Nothing pushed the other way, and agents that follow the size rules split code
finer than it needs: a package of five modules of ten lines each, every one
with its own import path and namespace. Ousterhout calls a unit that adds an
interface without much functionality behind it a shallow module, and warns
that splitting into many of them multiplies interfaces
[@ousterhout2018philosophy, ch. 4]; Fowler's Lazy Element is the smell of a
unit that no longer pays for itself [@fowler2018refactoring, ch. 3]. Modules
that are always used together are better together
[@ousterhout2018philosophy, ch. 9]. The rule measures size and count, not the
interface itself, and asks whether each module is really its own thing.

The package must hold at least ``min_modules`` members; total at most two
thirds of SIZE-001's warning, so the merge never trips SIZE-001; have at least
half its members below 20 lines of code; have every member imported by
production code (a module nothing imports is loaded by name: a plugin, a
Django command); have no subdirectory holding code; not repeat the module
names of two or more other packages (a per-feature layout); and not close a
new import cycle when merged. Framework-loaded packages (``apps.py``,
``settings.py``, ``urls.py``...), generated code, ``migrations/``,
``alembic/`` and test packages are exempt. See docs/RULES.md for the
measurements behind each condition.

Advisory and default-off; the ``strict`` profile turns it on. Settings that
decide which merges are legal (the size limits, the layers, the ports
directory, the adapter roots, the composition roots) are read from their own
checks' tables (see ``lanorme.checkconfig``).

Run:
    lanorme check . --check=shallow_modules
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import is_flag_set, read_int, read_str, read_str_list
from lanorme.checks.file_limits import FILE_ERROR_LINES, FILE_WARN_LINES
from lanorme.checks.layer_deps import COMPOSITION_ROOT_GLOBS, LAYERS
from lanorme.checks.merge_targets import (
    TINY_CODE_LINES,
    FixContext,
    PackageMember,
    PackagePlacement,
    build_fix,
    build_message,
    choose_merge_target,
    list_directories_read_by_path,
)
from lanorme.checks.package_members import (
    PackageCandidate,
    ScannedTree,
    collect_package_members,
    find_directory,
    is_skipped_package,
    is_structurally_shallow,
    list_staying_files,
    read_scanned_tree,
)
from lanorme.checks.port_coverage import (
    DEFAULT_ADAPTER_ROOTS,
    DEFAULT_COMPOSITION_ROOT,
    DEFAULT_PORTS_DIR,
)
from lanorme.line_counts import count_effective_lines
from lanorme.module_graph import PACKAGE_FILE
from lanorme.scan import Scan

DEFAULT_MIN_MODULES = 3


@dataclass
class ShallowModulesCheck:
    """SHALLOW-001: small packages split into many shallow modules (advisory, default-off)."""

    name: str = "shallow_modules"
    description: str = (
        "Small packages split into many shallow modules, with the module to merge them into"
    )
    scope = "tree"  # a package is judged by its importers anywhere in the tree
    enabled: bool = False
    min_modules: int = DEFAULT_MIN_MODULES
    file_warn_lines: int = FILE_WARN_LINES
    file_error_lines: int = FILE_ERROR_LINES
    layers: tuple[str, ...] = LAYERS
    layer_composition_root: tuple[str, ...] = COMPOSITION_ROOT_GLOBS
    ports_dir: str = DEFAULT_PORTS_DIR
    adapter_roots: tuple[str, ...] = DEFAULT_ADAPTER_ROOTS
    port_composition_root: tuple[str, ...] = DEFAULT_COMPOSITION_ROOT
    rules: list[str] = field(
        default_factory=lambda: [
            "SHALLOW-001: A small package is split into many shallow modules",
        ],
    )
    # A question about a design choice, never a build failure: promote = ["ALL"]
    # (the strict profile) leaves it a warning; naming it promotes it.
    advisory_codes: ClassVar[frozenset[str]] = frozenset({"SHALLOW-001"})
    settings_keys: ClassVar[frozenset[str]] = frozenset(
        {
            "enabled",
            "min_modules",
            "file_warn_lines",
            "file_error_lines",
            "layers",
            "layer_composition_root",
            "ports_dir",
            "adapter_roots",
            "port_composition_root",
        },
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.shallow_modules]``, mirrored keys included (see checkconfig)."""
        self.enabled = is_flag_set(settings=settings, key="enabled", default=self.enabled)
        min_modules = read_int(settings=settings, key="min_modules", default=self.min_modules)
        if min_modules < 2:
            raise ValueError(f"'min_modules' must be at least 2, got {min_modules}")
        self.min_modules = min_modules
        for key in ("file_warn_lines", "file_error_lines"):
            setattr(self, key, read_int(settings=settings, key=key, default=getattr(self, key)))
        self.layer_composition_root = read_str_list(
            settings=settings,
            key="layer_composition_root",
            default=self.layer_composition_root,
        )
        ports_dir = read_str(settings=settings, key="ports_dir", default="")
        if ports_dir:
            self.ports_dir = ports_dir.replace("\\", "/").strip("/")
        for key in ("layers", "adapter_roots", "port_composition_root"):
            value = read_str_list(settings=settings, key=key, default=getattr(self, key))
            if value:
                setattr(self, key, value)

    @property
    def merge_budget(self) -> int:
        """Two thirds of SIZE-001's effective warning line, so a merge never reaches it."""
        return 2 * min(self.file_warn_lines, self.file_error_lines) // 3

    def _find_candidate(self, *, package: str, tree: ScannedTree) -> PackageCandidate | None:
        """The package's members, when there are enough of them, small enough and mostly tiny."""
        modules = tree.by_directory.get(package, [])
        globs = (*self.layer_composition_root, *self.port_composition_root)
        found = collect_package_members(modules=modules, composition_globs=globs, tree=tree)
        if found is None or len(found) < self.min_modules:
            return None
        sized = [(module, count_effective_lines(source=module.source)) for module in found]
        if sum(lines for _module, lines in sized) > self.merge_budget:
            return None
        members = [
            PackageMember(relative=module.relative, lines=lines, code_lines=tree.count_code(module))
            for module, lines in sized
        ]
        tiny = sum(1 for member in members if member.code_lines < TINY_CODE_LINES)
        if 2 * tiny < len(members):
            return None
        return PackageCandidate(package=package, modules=modules, members=members, tiny_count=tiny)

    def _build_finding(self, *, candidate: PackageCandidate, tree: ScannedTree) -> Violation:
        package = candidate.package
        parent = find_directory(package)
        placement = PackagePlacement(
            package=package,
            is_top_level=parent not in tree.graph.packages,
            has_sibling_module=f"{package}.py" in tree.relatives,
            staying_files=list_staying_files(candidate=candidate, tree=tree),
        )
        directories = list_directories_read_by_path(
            layers=self.layers,
            ports_directory=self.ports_dir,
            adapter_roots=self.adapter_roots,
        )
        target = choose_merge_target(
            placement=placement,
            members=candidate.members,
            directories=directories,
        )
        node = tree.graph.nodes.get(f"{package}/{PACKAGE_FILE}")
        context = FixContext(
            package=package,
            dotted=node.dotted if node else package.replace("/", "."),
            members=candidate.members,
            warning_lines=min(self.file_warn_lines, self.file_error_lines),
        )
        return Violation(
            file=f"{package}/{PACKAGE_FILE}",
            line=1,
            rule="SHALLOW-001",
            message=build_message(
                package=package,
                members=candidate.members,
                tiny_count=candidate.tiny_count,
            ),
            fix=build_fix(context=context, target=target),
        )

    def check(self, scan: Scan) -> CheckResult:
        """Report every leaf package split finer than its code needs, with where to merge it."""
        if not self.enabled:
            return CheckResult.from_findings(check=self.name)
        tree = read_scanned_tree(scan=scan)
        warnings: list[Violation] = []
        for package in sorted(tree.graph.packages):
            if is_skipped_package(package=package, tree=tree):
                continue
            candidate = self._find_candidate(package=package, tree=tree)
            if candidate is None:
                continue
            if is_structurally_shallow(candidate=candidate, tree=tree):
                warnings.append(self._build_finding(candidate=candidate, tree=tree))
        return CheckResult.from_findings(check=self.name, warnings=warnings)


# Self-register on import.
register(ShallowModulesCheck())
