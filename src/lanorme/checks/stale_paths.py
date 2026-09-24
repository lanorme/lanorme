"""STALE-001: Stale path references in Python docstrings and comments.

When a project moves or renames a directory, references to the old path tend to
linger in docstrings and comments long after the code has moved. This check
flags any configured stale path token (matched both backtick-wrapped and as a
plain ``old/file.py`` reference) so the regressions get caught mechanically.

Configure the stale tokens in ``[tool.lanorme.stale_paths]``::

    [tool.lanorme.stale_paths]
    tokens = ["src/", "old_pkg/"]

With no configuration the check is inert (always PASS).

Boundary exemptions: files under ``tests/`` are skipped (fixtures legitimately
reference legacy layouts).

Run:
    lanorme check . --check=stale_paths
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import read_str_list
from lanorme.sources import Module, iter_parsed_modules, locate

# Default is empty → the check is inert until configured.
_STALE_TOKENS: tuple[str, ...] = ()
_EXEMPT_PATH_FRAGMENTS = ("tests/",)


def _compile_patterns(tokens: tuple[str, ...]) -> list[re.Pattern[str]]:
    """Build backtick-wrapped and plain-path matchers for each stale token."""
    patterns: list[re.Pattern[str]] = []
    for token in tokens:
        escaped = re.escape(token)
        patterns.append(re.compile(rf"`{escaped}[A-Za-z0-9_./<>-]*`"))
        patterns.append(re.compile(rf"\b{escaped}[A-Za-z0-9_]+\.py\b"))
    return patterns


def _is_exempt(*, relative_path: str) -> bool:
    normalised = relative_path.replace("\\", "/")
    return any(normalised.startswith(p) for p in _EXEMPT_PATH_FRAGMENTS)


def _scan_docstring(
    *,
    const: ast.Constant,
    relative_file: str,
    patterns: list[re.Pattern[str]],
) -> list[Violation]:
    findings: list[Violation] = []
    for lineno_0, line in enumerate(const.value.splitlines()):
        for pattern in patterns:
            for match in pattern.finditer(line):
                findings.append(
                    Violation(
                        file=relative_file,
                        line=const.lineno + lineno_0,
                        rule="STALE-001",
                        message=f"Stale path reference '{match.group(0)}'",
                        fix=f"Update '{match.group(0)}' to the current path",
                        **locate(const),
                    )
                )
    return findings


def _iter_comments(source: str) -> list[tuple[int, int, str]]:
    """Yield ``(lineno, column, comment_text)`` for every real comment in *source*.

    Uses :mod:`tokenize` so that a ``#`` inside a string literal is never
    mistaken for a comment. Un-tokenisable sources yield no comments (a graceful
    fallback that keeps the check silent rather than risking a false positive).
    """
    comments: list[tuple[int, int, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                comments.append((tok.start[0], tok.start[1], tok.string))
    except (tokenize.TokenError, IndentationError):
        return []
    return comments


def _scan_file(*, module: Module, patterns: list[re.Pattern[str]]) -> list[Violation]:
    relative_file = module.relative
    violations: list[Violation] = []

    # Inline comments (tokenize-extracted, so a '#' in a string never counts).
    for lineno, column, comment_text in _iter_comments(module.source):
        for pattern in patterns:
            for match in pattern.finditer(comment_text):
                violations.append(
                    Violation(
                        file=relative_file,
                        line=lineno,
                        rule="STALE-001",
                        message=f"Stale path reference '{match.group(0)}' in comment",
                        fix=f"Update '{match.group(0)}' to the current path",
                        column=column,
                    )
                )

    # Docstrings, module, class, function.
    for node in module.index.collect(ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef):
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            violations.extend(
                _scan_docstring(
                    const=node.body[0].value, relative_file=relative_file, patterns=patterns
                )
            )

    return violations


@dataclass
class StalePathsCheck:
    """Catches stale path references in Python docstrings and comments."""

    settings_keys: ClassVar[frozenset[str]] = frozenset({"tokens"})

    name: str = "stale_paths"
    description: str = "Stale path references (old directory tokens) in Python source"
    tokens: tuple[str, ...] = _STALE_TOKENS
    rules: list[str] = field(
        default_factory=lambda: [
            "STALE-001: No references to configured stale path tokens in docstrings or comments",
        ]
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.stale_paths]`` configuration."""
        self.tokens = read_str_list(settings=settings, key="tokens")

    def run(self, *, src_root: str) -> CheckResult:
        violations: list[Violation] = []
        patterns = _compile_patterns(self.tokens)
        if not patterns:
            return CheckResult.from_findings(check=self.name)

        for module in iter_parsed_modules(Path(src_root)):
            if _is_exempt(relative_path=module.relative):
                continue
            violations.extend(_scan_file(module=module, patterns=patterns))

        return CheckResult.from_findings(check=self.name, violations=violations)



register(StalePathsCheck())
