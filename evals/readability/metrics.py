"""Readability measures LaNorme does not currently express as rules.

The eval asks a narrow question: when an agent writes code that a reviewer
would call unreadable, which of that unreadability is visible to LaNorme? To
answer it we need a second yardstick, one that measures the three things the
tool is silent about:

- ``naming``: identifiers too short to carry meaning.
- ``comments``: explanatory comment density, and docstrings that say nothing.
- ``expressions``: single expressions that hide a paragraph of logic.

These are measurements, not rules. Nothing here fails a build; the numbers
exist so the gap between "LaNorme is happy" and "a reviewer is not" can be
stated in figures rather than adjectives.
"""

from __future__ import annotations

import ast
import re
import tokenize
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

# Single letters that are conventional and readable in their idiom: loop
# counters, throwaway targets, and the maths-style axes used in numeric code.
CONVENTIONAL_SHORT_NAMES = frozenset({"_", "i", "j", "k", "n", "x", "y", "z", "db", "id", "fh", "op", "lr", "pk"})

# A comment that instructs tooling rather than a reader.
PRAGMA = re.compile(r"^#\s*(noqa|type:|pragma|pylint|mypy|ruff|fmt:|lanorme:|!)")

# Comprehension "load" counts the steps a reader has to hold at once: each
# filter ``if``, each generator past the first, each call, each ternary, each
# nested comprehension. Raw AST node counts do not work as a proxy: a plain
# ``{k: v for k, v in d.items() if v is not None}`` is already 25 nodes.
# Measured over the sonnet corpus (53 comprehensions) load runs median 1,
# p90 3, max 4, so a budget of 6 flags genuine outliers rather than idiom.
COMPREHENSION_LOAD_BUDGET = 6

# Docstrings at or below this word count restate the name and add nothing.
TRIVIAL_DOCSTRING_WORDS = 4

_COMPREHENSION_TYPES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


@dataclass
class FileMetrics:
    """Readability measures for one source file."""

    path: str
    effective_lines: int = 0
    identifiers: int = 0
    short_identifiers: list[str] = field(default_factory=list)
    explanatory_comments: int = 0
    definitions: int = 0
    documented_definitions: int = 0
    trivial_docstrings: int = 0
    heavy_comprehensions: list[int] = field(default_factory=list)
    max_comprehension_load: int = 0
    max_expression_depth: int = 0

    @property
    def undocumented_definitions(self) -> int:
        """Definitions carrying no docstring at all."""
        return self.definitions - self.documented_definitions

    def as_dict(self) -> dict[str, object]:
        """Flatten to JSON-friendly ratios alongside the raw counts."""
        per_hundred = 100 / self.effective_lines if self.effective_lines else 0.0
        return {
            "path": self.path,
            "effective_lines": self.effective_lines,
            "short_name_rate": _ratio(subset=self.short_identifiers, total=self.identifiers),
            "short_names": sorted(set(self.short_identifiers)),
            "comments_per_100_lines": round(self.explanatory_comments * per_hundred, 2),
            "docstring_coverage": _ratio_counts(part=self.documented_definitions, total=self.definitions),
            "undocumented_definitions": self.undocumented_definitions,
            "trivial_docstrings": self.trivial_docstrings,
            "heavy_comprehensions": len(self.heavy_comprehensions),
            "max_comprehension_load": self.max_comprehension_load,
            "max_expression_depth": self.max_expression_depth,
        }


def _ratio(*, subset: list[str], total: int) -> float:
    """Fraction of *total* covered by *subset*, 0.0 when there is nothing to divide."""
    return round(len(subset) / total, 3) if total else 0.0


def _ratio_counts(*, part: int, total: int) -> float:
    """Fraction *part* of *total*, 0.0 when there is nothing to divide."""
    return round(part / total, 3) if total else 0.0


def _is_short(*, name: str) -> bool:
    """True if *name* is too short to carry meaning in its own right."""
    if name in CONVENTIONAL_SHORT_NAMES or name.startswith("__"):
        return False
    return len(name.lstrip("_")) <= 2


def _expression_depth(*, node: ast.AST, depth: int = 0) -> int:
    """Deepest chain of nested expression nodes reachable from *node*."""
    children = [c for c in ast.iter_child_nodes(node) if isinstance(c, ast.expr)]
    if not children:
        return depth
    return max(_expression_depth(node=c, depth=depth + 1) for c in children)


def _declared_names(*, node: ast.AST) -> list[str]:
    """Names *node* introduces: parameters, assignment targets, definitions."""
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return [node.id]
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [node.name]
    return []


def _collect_names(*, tree: ast.Module, into: FileMetrics) -> None:
    """Count every declared identifier and record the ones too short to read."""
    for node in ast.walk(tree):
        for name in _declared_names(node=node):
            into.identifiers += 1
            if _is_short(name=name):
                into.short_identifiers.append(name)


def _collect_docstrings(*, tree: ast.Module, into: FileMetrics) -> None:
    """Measure how many definitions are documented, and how many say nothing."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        into.definitions += 1
        doc = ast.get_docstring(node)
        if doc is None:
            continue
        into.documented_definitions += 1
        if len(doc.split()) <= TRIVIAL_DOCSTRING_WORDS:
            into.trivial_docstrings += 1


def _comprehension_load(*, node: ast.expr) -> int:
    """Steps a reader must hold at once to follow one comprehension."""
    generators = getattr(node, "generators", [])
    inner = list(ast.walk(node))
    filters = sum(len(gen.ifs) for gen in generators)
    calls = sum(1 for child in inner if isinstance(child, ast.Call))
    ternaries = sum(1 for child in inner if isinstance(child, ast.IfExp))
    nested = sum(1 for child in inner if isinstance(child, _COMPREHENSION_TYPES)) - 1
    return filters + max(len(generators) - 1, 0) + calls + ternaries + nested


def _collect_expressions(*, tree: ast.Module, into: FileMetrics) -> None:
    """Record comprehension load and the deepest single expression."""
    for node in ast.walk(tree):
        if isinstance(node, _COMPREHENSION_TYPES):
            load = _comprehension_load(node=node)
            into.max_comprehension_load = max(into.max_comprehension_load, load)
            if load > COMPREHENSION_LOAD_BUDGET:
                into.heavy_comprehensions.append(load)
        if isinstance(node, ast.stmt):
            depth = _expression_depth(node=node)
            into.max_expression_depth = max(into.max_expression_depth, depth)


def _collect_comments(*, source: str, into: FileMetrics) -> None:
    """Count comments that address a reader, and effective (code) lines."""
    code_lines: set[int] = set()
    tokens = tokenize.tokenize(BytesIO(source.encode()).readline)
    for token in tokens:
        if token.type == tokenize.COMMENT:
            if not PRAGMA.match(token.string.strip()):
                into.explanatory_comments += 1
        elif token.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER):
            code_lines.add(token.start[0])
    into.effective_lines = len(code_lines)


def measure(*, path: Path, root: Path) -> FileMetrics:
    """Compute every readability measure for one Python file."""
    source = path.read_text(encoding="utf-8")
    result = FileMetrics(path=str(path.relative_to(root)))
    tree = ast.parse(source)
    _collect_names(tree=tree, into=result)
    _collect_docstrings(tree=tree, into=result)
    _collect_expressions(tree=tree, into=result)
    _collect_comments(source=source, into=result)
    return result
