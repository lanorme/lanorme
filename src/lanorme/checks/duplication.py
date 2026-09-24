"""DRY-001: Exact structural clone detection via AST normalization.

Checks:
    DRY-001  Functions with identical normalized AST bodies (>= 5 statements)
             are flagged as duplication candidates.

Normalization: variable names and string literals are replaced with placeholders
so that functions differing only in naming are detected as duplicates. The match
is exact modulo those placeholders: a single added statement, a reordering, a
changed number, or a renamed attribute defeats it. For the fuzzier near-duplicate
cases see the ``similarity`` check (SIMILAR-001).

Excludes: __init__.py, conftest.py, alembic/, migrations/, test_* prefixed files.

Run:
    lanorme check . --check=duplication
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Violation, register
from lanorme.sources import (
    TOO_DEEP,
    Module,
    Unparseable,
    iter_modules,
    skip_notice,
    span,
    unparseable_notice,
)

# Minimum number of statements in a function body to consider for duplication.
MIN_BODY_STATEMENTS = 5

# Files and directories excluded from scanning.
EXCLUDED_FILENAMES = {"__init__.py", "conftest.py"}
EXCLUDED_DIR_PARTS = {"alembic", "migrations"}


def _should_exclude(*, relative: Path) -> bool:
    """True if the file at *relative*, inside the scan root, is exempt.

    Matching the root-relative parts, not the absolute path's, keeps the
    user's filesystem above the root out of it: a checkout that happens to
    live under a ``migrations/`` directory is scanned like any other.
    """
    if relative.name in EXCLUDED_FILENAMES:
        return True
    if relative.name.startswith("test_"):
        return True
    return any(part in EXCLUDED_DIR_PARTS for part in relative.parts)


# Fields whose value is a name the normaliser replaces with a placeholder.
_NAME_FIELDS: dict[type[ast.AST], str] = {
    ast.Name: "id",
    ast.arg: "arg",
    ast.FunctionDef: "name",
    ast.AsyncFunctionDef: "name",
}


class _NormalisedDump:
    """Render a body as a dump with names and string literals abstracted away.

    Structurally identical functions produce the same string even when they
    use different variable names or string constants. Names are replaced by
    sequential placeholders in first-seen order and every string literal by
    one token. The tree is read, never copied or mutated: it is shared with
    every other check this run.
    """

    def __init__(self) -> None:
        self._name_map: dict[str, str] = {}

    def _placeholder(self, name: str) -> str:
        """Map a name to a sequential placeholder."""
        if name not in self._name_map:
            self._name_map[name] = f"_var{len(self._name_map)}"
        return self._name_map[name]

    def render(self, value: object) -> str:
        """The normalised dump of a node, a list of nodes, or a leaf value."""
        if isinstance(value, ast.AST):
            name_field = _NAME_FIELDS.get(type(value))
            parts: list[str] = []
            for field_name, child in ast.iter_fields(value):
                if field_name == name_field:
                    rendered = self._placeholder(str(child))
                elif isinstance(value, ast.Constant) and isinstance(child, str):
                    rendered = "_STR_"
                else:
                    rendered = self.render(child)
                parts.append(f"{field_name}={rendered}")
            return f"{type(value).__name__}({', '.join(parts)})"
        if isinstance(value, list):
            return "[" + ", ".join(self.render(item) for item in value) + "]"
        return repr(value)


def _normalize_function_body(*, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Return a normalized dump of a function's body for comparison."""
    return _NormalisedDump().render(func_node.body)


@dataclass(frozen=True)
class _FunctionLocation:
    """Tracks where a function was found for violation reporting."""

    file: str
    line: int
    name: str
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None


def _collect_functions(*, module: Module) -> list[tuple[str, _FunctionLocation]]:
    """Walk the AST and return (normalized_hash, location) for qualifying functions."""
    results: list[tuple[str, _FunctionLocation]] = []

    for node in module.index.functions:
        # Skip functions with fewer statements than the threshold.
        if len(node.body) < MIN_BODY_STATEMENTS:
            continue

        # Skip if the entire body is a single docstring + pass or similar trivial patterns.
        normalized = _normalize_function_body(func_node=node)
        location = _FunctionLocation(
            file=module.relative,
            line=node.lineno,
            name=node.name,
            **span(node),
        )
        results.append((normalized, location))

    return results


def _build_violations(
    *,
    groups: dict[str, list[_FunctionLocation]],
) -> list[Violation]:
    """Create a violation for each function that has a near-duplicate elsewhere."""
    violations: list[Violation] = []

    for locations in groups.values():
        if len(locations) < 2:
            continue

        # Sort by file then line for deterministic output.
        sorted_locs = sorted(locations, key=lambda loc: (loc.file, loc.line))
        other_locations = [f"{loc.file}:{loc.line} ({loc.name})" for loc in sorted_locs]

        for loc in sorted_locs:
            peers = [o for o in other_locations if o != f"{loc.file}:{loc.line} ({loc.name})"]
            violations.append(
                Violation(
                    file=loc.file,
                    line=loc.line,
                    rule="DRY-001: Near-duplicate function body detected",
                    message=(
                        f"Function '{loc.name}' has a near-duplicate body "
                        f"matching: {', '.join(peers)}"
                    ),
                    fix="Extract shared logic into a common helper function",
                    column=loc.column,
                    end_line=loc.end_line,
                    end_column=loc.end_column,
                ),
            )

    return violations


@dataclass
class DuplicationCheck:
    """Detects near-duplicate function bodies via AST normalization."""

    name: str = "duplication"
    description: str = "Near-duplicate function detection (DRY enforcement)"
    scope = "tree"  # groups functions across files; partitioning would hide split pairs
    rules: list[str] = field(
        default_factory=lambda: [
            "DRY-001: Functions with identical normalized AST bodies (>= 5 statements) are duplicates",
        ],
    )

    def run(self, *, src_root: str) -> CheckResult:
        """Scan all Python files under src/ and detect near-duplicate functions."""
        warnings: list[Violation] = []

        # Map normalized body hash -> list of locations.
        body_groups: dict[str, list[_FunctionLocation]] = defaultdict(list)

        for module in iter_modules(Path(src_root)):
            if _should_exclude(relative=Path(module.relative)):
                continue
            if isinstance(module, Unparseable):
                warnings.append(unparseable_notice(prefix="DRY", failure=module))
                continue

            relative_file = module.relative

            try:
                collected = _collect_functions(module=module)
            except RecursionError:
                # A deeply nested AST overflows the deepcopy used to normalise a
                # body. Skip the file rather than crash the whole run.
                warnings.append(
                    skip_notice(
                        prefix="DRY", file=relative_file, name=module.path.name, reason=TOO_DEEP
                    )
                )
                continue

            for normalized_hash, location in collected:
                body_groups[normalized_hash].append(location)

        violations = _build_violations(groups=body_groups)

        return CheckResult.from_findings(check=self.name, violations=violations, warnings=warnings)


# Self-register on import.
register(DuplicationCheck())
