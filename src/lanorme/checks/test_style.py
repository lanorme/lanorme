"""AAA-001 and AAA-002: test-style enforcement for pytest-style suites.

The check applies only to test functions in test files. A test function is a
function whose name starts with ``test_`` defined inside a file whose stem
starts with ``test_`` or ends with ``_test``.

    AAA-001  Each non-trivial test function must have inline AAA section
             comments (Arrange/Act/Assert, or Given/When/Then). The default
             requires at least two of the three markers; trivial tests
             (default <= 3 statements) are exempt because they read fine as
             a single block.
    AAA-002  Test functions in the same file must not share an identical
             prefix of statements (the "arrange" block). Repeated setup is
             a DRY violation, extract it into a fixture or helper.

Both rules are opt-in (the check ships default-off). Enable and configure with::

    [tool.lanorme.test_style]
    enabled = true
    min_statements = 3           # tests shorter than this skip AAA-001
    required_markers = 2         # 1..3; how many of A/A/A must appear
    synonyms = ["setup", "given", "when", "then"]  # extra marker aliases
    dry_prefix_statements = 3    # AAA-002 prefix length

Run:
    lanorme check . --check=test_style
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import int_setting, is_flag_set, str_list_setting
from lanorme.sources import Module, parsed_modules, span

# Default marker vocabulary. AAA + BDD + a few common aliases.
_DEFAULT_MARKERS = ("arrange", "act", "assert", "given", "when", "then")

# Three logical sections; each maps to a set of accepted aliases. AAA-001
# requires the test to hit at least ``required_markers`` distinct sections.
_SECTION_ALIASES: dict[str, frozenset[str]] = {
    "arrange": frozenset({"arrange", "setup", "given"}),
    "act": frozenset({"act", "when", "exercise", "call"}),
    "assert": frozenset({"assert", "then", "expect", "verify"}),
}

# Directories that look like tests but are not (fixtures, factories, conftest).
_TEST_NON_TEST_STEMS = frozenset({"conftest", "__init__", "fixtures", "factories"})


def _is_test_file(*, path: Path) -> bool:
    """True if *path* looks like a pytest test module."""
    stem = path.stem
    if stem in _TEST_NON_TEST_STEMS:
        return False
    return stem.startswith("test_") or stem.endswith("_test")


def _is_test_function(*, node: ast.AST) -> bool:
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return False
    if not node.name.startswith("test_"):
        return False
    # A function decorated with @pytest.fixture is a fixture, not a test.
    for dec in node.decorator_list:
        if isinstance(dec, ast.Attribute) and dec.attr == "fixture":
            return False
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "fixture":
            return False
    return True


def _statements_in(*, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    """Body statements minus a leading docstring (which is documentation, not setup)."""
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return body


def _section_markers_in(
    *,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
    marker_re: re.Pattern[str],
    alias_to_section: dict[str, str],
) -> set[str]:
    """Return the set of AAA sections whose marker comments appear inside *node*."""
    end = node.end_lineno or node.lineno
    sections: set[str] = set()
    for lineno in range(node.lineno, end + 1):
        line = source_lines[lineno - 1] if 0 < lineno <= len(source_lines) else ""
        match = marker_re.match(line)
        if match is None:
            continue
        alias = match.group("alias").lower()
        section = alias_to_section.get(alias)
        if section is not None:
            sections.add(section)
    return sections


def _normalize_prefix(*, statements: list[ast.stmt], prefix_len: int) -> str | None:
    """AST-dump the first *prefix_len* statements; None if the test is shorter."""
    if len(statements) < prefix_len:
        return None
    return "|".join(ast.dump(s, annotate_fields=False) for s in statements[:prefix_len])


@dataclass
class TestStyleCheck:
    """Enforce clearly-commented AAA structure and DRY arrange blocks in tests."""

    # pytest's default test collector picks up any class named Test*; this
    # class is the check implementation, not a test class.
    __test__ = False

    settings_keys: ClassVar[frozenset[str]] = frozenset(
        {"enabled", "min_statements", "required_markers", "dry_prefix_statements", "synonyms"}
    )

    name: str = "test_style"
    description: str = "AAA-style and DRY enforcement for pytest test suites"
    # Ships default-off: the audit flagged AAA-001's comment-marker
    # mandate as something that will fire on essentially every existing
    # pytest suite. Opt in via ``[tool.lanorme.test_style] enabled = true``.
    enabled: bool = False
    min_statements: int = 3
    required_markers: int = 2
    dry_prefix_statements: int = 3
    extra_synonyms: tuple[str, ...] = ()
    rules: list[str] = field(
        default_factory=lambda: [
            "AAA-001: Test functions must carry AAA (or Given/When/Then) section comments",
            "AAA-002: Test functions in the same file must not share an identical arrange prefix",
        ]
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.test_style]`` configuration."""
        self.enabled = is_flag_set(settings=settings, key="enabled", default=self.enabled)
        self.min_statements = int_setting(
            settings=settings, key="min_statements", default=self.min_statements
        )
        markers = int_setting(settings=settings, key="required_markers", default=self.required_markers)
        self.required_markers = max(1, min(3, markers))
        self.dry_prefix_statements = int_setting(
            settings=settings, key="dry_prefix_statements", default=self.dry_prefix_statements
        )
        self.extra_synonyms = tuple(
            synonym.lower()
            for synonym in str_list_setting(settings=settings, key="synonyms", default=self.extra_synonyms)
        )

    def _build_alias_map(self) -> tuple[re.Pattern[str], dict[str, str]]:
        """Compile the comment-marker regex and the alias-to-section table."""
        alias_to_section: dict[str, str] = {}
        for section, aliases in _SECTION_ALIASES.items():
            for alias in aliases:
                alias_to_section[alias] = section
        for extra in self.extra_synonyms:
            # Unknown synonyms default to whichever section's base alias they match.
            for section, aliases in _SECTION_ALIASES.items():
                if extra.startswith(tuple(aliases)):
                    alias_to_section[extra] = section
                    break
            else:
                alias_to_section.setdefault(extra, "arrange")
        all_aliases = sorted(alias_to_section, key=len, reverse=True)
        pattern = re.compile(
            rf"^\s*#\s*(?P<alias>{'|'.join(re.escape(a) for a in all_aliases)})\b[\s:.\-]*",
            re.IGNORECASE,
        )
        return pattern, alias_to_section

    def _aaa_violations(
        self,
        *,
        module: Module,
        marker_re: re.Pattern[str],
        alias_to_section: dict[str, str],
    ) -> list[Violation]:
        found: list[Violation] = []
        source_lines = module.lines
        for node in module.index.functions:
            if not _is_test_function(node=node):
                continue
            statements = _statements_in(node=node)
            if len(statements) <= self.min_statements:
                continue
            sections = _section_markers_in(
                node=node,
                source_lines=source_lines,
                marker_re=marker_re,
                alias_to_section=alias_to_section,
            )
            if len(sections) >= self.required_markers:
                continue
            present = ", ".join(sorted(sections)) if sections else "none"
            found.append(
                Violation(
                    file=module.relative,
                    line=node.lineno,
                    rule="AAA-001",
                    message=(
                        f"Test '{node.name}' has {len(statements)} statements but "
                        f"only {len(sections)} AAA section marker(s) ({present}); "
                        f"need >= {self.required_markers}"
                    ),
                    fix="Add inline '# Arrange', '# Act', '# Assert' (or Given/When/Then) markers",
                    **span(node),
                )
            )
        return found

    def _dry_violations(self, *, module: Module) -> list[Violation]:
        """Flag any two test functions that share the same arrange prefix."""
        per_prefix: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        for node in module.index.functions:
            if not _is_test_function(node=node):
                continue
            statements = _statements_in(node=node)
            digest = _normalize_prefix(
                statements=statements, prefix_len=self.dry_prefix_statements
            )
            if digest is None:
                continue
            per_prefix.setdefault(digest, []).append(node)
        found: list[Violation] = []
        for nodes in per_prefix.values():
            if len(nodes) < 2:
                continue
            for node in nodes:
                found.append(
                    Violation(
                        file=module.relative,
                        line=node.lineno,
                        rule="AAA-002",
                        message=(
                            f"Test '{node.name}' shares its first "
                            f"{self.dry_prefix_statements} statements with "
                            f"{len(nodes) - 1} other test(s) in this file"
                        ),
                        fix="Extract the repeated arrange block into a pytest fixture or helper",
                        **span(node),
                    )
                )
        return found

    def run(self, *, src_root: str) -> CheckResult:
        if not self.enabled:
            return CheckResult.from_findings(check=self.name)
        marker_re, alias_to_section = self._build_alias_map()
        violations: list[Violation] = []
        for module in parsed_modules(Path(src_root)):
            if not _is_test_file(path=module.path):
                continue
            violations.extend(
                self._aaa_violations(
                    module=module, marker_re=marker_re, alias_to_section=alias_to_section
                )
            )
            violations.extend(self._dry_violations(module=module))
        return CheckResult.from_findings(check=self.name, violations=violations)



register(TestStyleCheck())
