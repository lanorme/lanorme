"""TESTFILE-001: every production module must have a corresponding test.

A single advisory (WARNING) rule, surfaced when a file in one of the hardwired
production directories lacks a matching ``test_*.py`` partner under
``tests/integration/``. AAA-style test checks live in the ``test_style`` check.

Run:
    lanorme check . --check=test_coverage
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register


# ---------------------------------------------------------------------------
# TESTFILE-001 helpers
# ---------------------------------------------------------------------------


"""Directories under src/ that contain testable production modules.

Each entry maps a directory (relative to src/) to the import path pattern
used when searching test files for corresponding imports. Modules in these
directories should each have at least one test.
"""
_TESTABLE_DIRS: list[tuple[str, str]] = [
    ("api/v1/endpoints", "endpoints"),
    ("application/services", "services"),
    ("application/commands", "commands"),
    ("application/queries", "queries"),
    ("infrastructure/repositories", "infrastructure.repositories"),
    ("infrastructure/signing", "infrastructure.signing"),
    ("infrastructure/secrets", "infrastructure.secrets"),
]

# Modules that are intentionally untested (pure Protocol definitions,
# config files, composition roots, etc.).
_EXEMPT_MODULES: set[str] = {
    "dependencies",  # DI wiring, tested via endpoint tests
    "main",  # App composition root
    "logging",  # Logging setup
    "session",  # DB session factory
}


def _find_production_modules(*, src_root: str) -> list[tuple[str, str, str]]:
    """Return testable production modules as (relative_path, name, import_hint)."""
    modules: list[tuple[str, str, str]] = []
    src_path = Path(src_root)

    for dir_rel, import_prefix in _TESTABLE_DIRS:
        target_dir = src_path / dir_rel
        if not target_dir.is_dir():
            continue

        for py_file in sorted(target_dir.glob("*.py")):
            name = py_file.stem
            if name.startswith("_"):
                continue
            if name in _EXEMPT_MODULES:
                continue

            rel_path = str(py_file.relative_to(src_path.parent))
            modules.append((rel_path, name, import_prefix))

    return modules


def _find_test_files(*, backend_root: Path) -> list[Path]:
    """Return all test_*.py files under tests/integration/."""
    tests_dir = backend_root / "tests" / "integration"
    if not tests_dir.is_dir():
        return []
    return sorted(tests_dir.glob("test_*.py"))


def _dotted_import_paths(source: str) -> list[str] | None:
    """Reconstruct the dotted import paths of a test file from its AST.

    Returns one string per imported target (e.g. ``app.services.billing``),
    or ``None`` if the source cannot be parsed, signalling the caller to fall
    back to a raw-text scan.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    paths: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            paths.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            paths.append(node.module)
            paths.extend(f"{node.module}.{alias.name}" for alias in node.names)

    return paths


def _import_covers_module(
    *,
    module_name: str,
    import_hint: str,
    contents: str,
    import_paths: list[str] | None,
) -> bool:
    """True if a real import in one test file targets the module.

    ``import_paths`` are the dotted paths reconstructed from the file's AST;
    when it is ``None`` (an unparseable file) we fall back to a permissive
    raw-text scan so an unreadable test is never treated as missing coverage.
    """
    needle = f"{import_hint}.{module_name}"
    if import_paths is None:
        return needle in contents
    return any(needle in path for path in import_paths)


def _module_has_test(
    *,
    module_name: str,
    import_hint: str,
    test_stems: set[str],
    test_file_imports: dict[str, tuple[str, list[str] | None]],
) -> bool:
    """True if any test file targets the module by name or by import."""
    if f"test_{module_name}" in test_stems:
        return True

    parts = module_name.split("_")
    if len(parts) > 1:
        shortened = "_".join(parts[:-1])
        if f"test_{shortened}" in test_stems:
            return True

    for contents, import_paths in test_file_imports.values():
        if _import_covers_module(
            module_name=module_name,
            import_hint=import_hint,
            contents=contents,
            import_paths=import_paths,
        ):
            return True

    return False


def _check_module_coverage(
    *,
    src_root: str,
    backend_root: Path,
) -> list[Violation]:
    """TESTFILE-001: verify every production module has a corresponding test."""
    modules = _find_production_modules(src_root=src_root)
    test_files = _find_test_files(backend_root=backend_root)
    test_stems = {f.stem for f in test_files}

    # Parse each test file once: keep its raw text and the dotted import paths
    # reconstructed from its AST (or None when it cannot be parsed).
    test_file_imports: dict[str, tuple[str, list[str] | None]] = {}
    for tf in test_files:
        try:
            contents = tf.read_text(encoding="utf-8")
        except OSError:
            continue
        test_file_imports[tf.stem] = (contents, _dotted_import_paths(contents))

    warnings: list[Violation] = []
    for rel_path, name, import_hint in modules:
        if not _module_has_test(
            module_name=name,
            import_hint=import_hint,
            test_stems=test_stems,
            test_file_imports=test_file_imports,
        ):
            warnings.append(
                Violation(
                    file=rel_path,
                    line=1,
                    rule="TESTFILE-001: Every production module must have a corresponding test",
                    message=f"No test file found for module '{name}'",
                    fix=(
                        f"Create tests/integration/test_{name}.py with at least one "
                        f"integration test for {name}"
                    ),
                ),
            )

    return warnings


@dataclass
class TestCoverageCheck:
    """Validates that every production module has a corresponding test file."""

    name: str = "test_coverage"
    description: str = "Test coverage: every production module has a test"
    scope = "tree"  # needs the whole test-file set to know a module is covered
    rules: list[str] = field(
        default_factory=lambda: [
            "TESTFILE-001: Every production module must have a corresponding test",
        ],
    )

    def run(self, *, src_root: str) -> CheckResult:
        """Run the coverage check and return advisory warnings."""
        backend_root = Path(src_root).parent
        coverage_warnings = _check_module_coverage(
            src_root=src_root, backend_root=backend_root
        )
        status = Status.WARN if coverage_warnings else Status.PASS
        return CheckResult(
            check=self.name,
            status=status,
            warnings=coverage_warnings,
        )


register(TestCoverageCheck())
