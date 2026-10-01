"""Which modules of a package count towards SHALLOW-001, and the conditions it checks.

A helper of the ``shallow_modules`` check; it registers nothing. It reads the
tree once (:func:`read_scanned_tree`), decides which files of a package are
members (:func:`collect_package_members`), and holds the structural conditions
a small, mostly tiny package must also meet before it is reported:

- **Reachable.** Production code imports every member. A module nothing imports,
  or only tests import, is loaded by name: a Django command, a template tag
  library, a plugin, an entry point.
- **Leaf.** No subdirectory holds code or an unparseable file. An empty or
  docstring-only subpackage does not count, so adding one cannot hide a
  package.
- **Not a repeated sibling layout.** Two or more other packages share at least
  half of its module names: a per-feature layout such as netbox's
  ``*/graphql`` packages, which the project repeats on purpose.
- **No new import cycle.** Merging the package's modules must not put the merged
  module on an import cycle, which only a function-local import (IMPORT-001)
  could then break.
"""

from __future__ import annotations

import fnmatch
import math
from collections import defaultdict
from dataclasses import dataclass, field

from lanorme.checks.file_limits import EXCLUDED_DIR_PARTS
from lanorme.checks.merge_targets import PACKAGE_FILE, PackageMember
from lanorme.line_counts import count_code_lines
from lanorme.module_graph import ModuleGraph, build_module_graph, is_reexport_only
from lanorme.paths import is_test_file
from lanorme.scan import Scan
from lanorme.sources import Module, iter_modules

# A package repeating the module names of this many other packages is a
# per-feature layout (netbox's ``*/graphql``), not an over-split.
REPEATED_LAYOUT_PACKAGES = 2

# Module stems a framework loads by name: Django, Celery, ASGI and WSGI.
FRAMEWORK_MODULE_STEMS = frozenset({"admin", "apps", "asgi", "settings", "tasks", "urls", "wsgi"})
# Scripts that sit in a package without being part of its interface.
SCRIPT_STEMS = frozenset({"__main__", "manage", "setup"})
# Generated files, by name or by a header marker in their first lines.
GENERATED_FILE_NAMES = frozenset({"_version.py"})
GENERATED_SUFFIXES = ("_pb2.py", "_pb2_grpc.py")
GENERATED_MARKER = "do not edit"
HEADER_LINES = 5


def find_directory(relative: str) -> str:
    """The directory of a root-relative path, ``""`` for a file at the root."""
    return relative.rsplit("/", 1)[0] if "/" in relative else ""


def _find_stem(relative: str) -> str:
    return relative.rsplit("/", 1)[-1].removesuffix(".py")


@dataclass
class ScannedTree:
    """One run's modules by directory, the import graph, and the per-file facts read lazily."""

    graph: ModuleGraph
    by_directory: dict[str, list[Module]]
    broken_directories: set[str]
    relatives: frozenset[str]
    code_lines: dict[str, int] = field(default_factory=dict)
    sibling_stems: dict[str, frozenset[str]] | None = None

    def count_code(self, module: Module) -> int:
        """The module's lines of code, docstrings left out, computed once."""
        if module.relative not in self.code_lines:
            self.code_lines[module.relative] = count_code_lines(module=module)
        return self.code_lines[module.relative]


def read_scanned_tree(*, scan: Scan) -> ScannedTree:
    """Read every module once, set the unreadable ones' directories aside, build the graph.

    A file too deeply nested to classify is treated like one that does not
    parse: its package is skipped rather than the run failing.
    """
    modules: list[Module] = []
    broken: set[str] = set()
    for module in iter_modules(scan.root):
        if isinstance(module, Module):
            try:
                is_reexport_only(tree=module.tree)
            except RecursionError:
                broken.add(find_directory(module.relative))
                continue
            modules.append(module)
        else:
            broken.add(find_directory(module.relative))
    by_directory: dict[str, list[Module]] = defaultdict(list)
    for module in modules:
        by_directory[find_directory(module.relative)].append(module)
    return ScannedTree(
        graph=build_module_graph(modules=modules),
        by_directory=dict(by_directory),
        broken_directories=broken,
        relatives=frozenset(module.relative for module in modules),
    )


def is_skipped_package(*, package: str, tree: ScannedTree) -> bool:
    """Migrations, alembic, test packages and packages holding an unparseable file."""
    if any(part in EXCLUDED_DIR_PARTS for part in package.split("/")):
        return True
    return is_test_file(f"{package}/{PACKAGE_FILE}") or package in tree.broken_directories


def _is_exempting_file(module: Module) -> bool:
    """True for generated code and for a module a framework loads by name."""
    name = module.relative.rsplit("/", 1)[-1]
    if name in GENERATED_FILE_NAMES or name.endswith(GENERATED_SUFFIXES):
        return True
    if _find_stem(module.relative) in FRAMEWORK_MODULE_STEMS:
        return True
    return GENERATED_MARKER in "\n".join(module.lines[:HEADER_LINES]).lower()


def _is_member(*, module: Module, composition_globs: tuple[str, ...]) -> bool:
    """True for a module that is part of the package's own code.

    ``__init__.py`` is a member when it holds more than re-exports. Tests,
    scripts, non-importable names, composition roots (wiring LAYER-005 and
    PORT-003 find by name) and re-export shims are not.
    """
    relative, stem = module.relative, _find_stem(module.relative)
    if stem != "__init__":
        if is_test_file(relative) or stem in SCRIPT_STEMS or not stem.isidentifier():
            return False
        if any(
            fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(relative, f"*/{pattern}")
            for pattern in composition_globs
        ):
            return False
    return not is_reexport_only(tree=module.tree)


def collect_package_members(
    *,
    modules: list[Module],
    composition_globs: tuple[str, ...],
) -> list[Module] | None:
    """The modules of one package that count towards its split; ``None`` when it is exempt."""
    if any(_is_exempting_file(module) for module in modules):
        return None
    return [
        module
        for module in modules
        if _is_member(module=module, composition_globs=composition_globs)
    ]


@dataclass(frozen=True)
class PackageCandidate:
    """A package under review: every module directly in it, and its sized members."""

    package: str
    modules: list[Module]
    members: list[PackageMember]


def is_reachable(*, candidate: PackageCandidate, tree: ScannedTree) -> bool:
    """True when production code imports every member; else something loads one by name."""
    for member in candidate.members:
        if member.is_package_file:
            continue
        importers = tree.graph.find_importers(member.relative)
        if not any(not is_test_file(importer) for importer in importers):
            return False
    return True


def is_leaf(*, candidate: PackageCandidate, tree: ScannedTree) -> bool:
    """True when no subdirectory holds code or an unparseable file; empty ones do not count."""
    prefix = f"{candidate.package}/"
    if any(directory.startswith(prefix) for directory in tree.broken_directories):
        return False
    return not any(
        tree.count_code(module) > 0
        for directory, modules in tree.by_directory.items()
        if directory.startswith(prefix)
        for module in modules
    )


def _build_sibling_stems(*, tree: ScannedTree) -> dict[str, frozenset[str]]:
    """The non-test, non-``__init__`` module stems directly in each package."""
    stems: dict[str, frozenset[str]] = {}
    for package in tree.graph.packages:
        found = {
            _find_stem(module.relative)
            for module in tree.by_directory.get(package, ())
            if not is_test_file(module.relative) and _find_stem(module.relative) != "__init__"
        }
        if found:
            stems[package] = frozenset(found)
    return stems


def is_repeated_sibling_layout(*, candidate: PackageCandidate, tree: ScannedTree) -> bool:
    """True when two or more other packages share at least half of this package's module names."""
    if tree.sibling_stems is None:
        tree.sibling_stems = _build_sibling_stems(tree=tree)
    own = tree.sibling_stems.get(candidate.package, frozenset())
    if len(own) < 2:
        return False
    needed = max(2, math.ceil(len(own) / 2))
    repeats = sum(
        1
        for package, stems in tree.sibling_stems.items()
        if package != candidate.package and len(own & stems) >= needed
    )
    return repeats >= REPEATED_LAYOUT_PACKAGES


def is_structurally_shallow(*, candidate: PackageCandidate, tree: ScannedTree) -> bool:
    """The reachable, leaf, not-a-repeated-layout and no-new-cycle conditions, cheapest first."""
    if not is_reachable(candidate=candidate, tree=tree) or not is_leaf(
        candidate=candidate,
        tree=tree,
    ):
        return False
    if is_repeated_sibling_layout(candidate=candidate, tree=tree):
        return False
    group = frozenset(module.relative for module in candidate.modules)
    return tree.graph.find_cycle_through(group=group) is None
