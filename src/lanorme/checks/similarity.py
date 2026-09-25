"""SIMILAR-001: structural near-duplicate detection (advisory, default-off).

The definition. Two functions are near-duplicates when they carry out the same
operations in the same control-flow positions and differ only in data and in
drift: variable, attribute and keyword names, numbers, and one or two
statements inserted, removed or reordered. A pair whose statements differ in
what they do (an operator flipped, a called name changed, a statement moved
into a new branch) is two functions. So is a pair that differs in the string
literals it carries: keys, column names, formats and messages are the content
a body is about, and parallel builders that share a shape but not their
strings are boilerplate, not a clone. DRY-001 catches the exact clone
(identical modulo variable names and strings) and fails the build; SIMILAR-001
fills the gap the exact match leaves, and only warns.

Each function is fingerprinted as:

  - a sequence of STATEMENT LINES, one per statement at every nesting depth:
    the depth, the statement kind, and the abstracted tokens of the
    statement's own expressions (a name is ``v``, an attribute access
    ``attr``, a number ``N``, a string ``S``; operator kinds and call arities
    are kept, keyword names are dropped);
  - the multiset of OPERATIONS: each statement's depth, kind and operators;
  - the multiset of CALLED NAMES: bare-call ids and method names; a call to a
    name the function binds itself (a parameter, a local) is a call through a
    variable and is abstracted, as DRY-001 abstracts it;
  - the multiset of STRING LITERALS, less the message strings passed
    positionally to a logging or print call, whose rewording is not drift in
    what the function does;
  - the multiset of ACCESSED ATTRIBUTE names.

A pair is flagged when every gate passes:

  ``struct_ratio``   ``difflib`` ratio over the statement lines: high when
                     the two bodies align up to a few inserted, removed or
                     reordered statements.
  ``op_jaccard``     the share of the smaller side's operations the other side
                     also carries: 1.0 means every statement keeps its
                     operators and its control-flow position, so a flipped
                     operator or a statement moved into a new branch fails it
                     while an inserted statement does not.
  ``call_jaccard``   the same share over called names.
  ``str_jaccard``    the same share over string literals: an inserted
                     statement may bring new strings, a changed key may not.
  ``attr_jaccard``   weighted Jaccard over attribute names, rejecting the
                     (near) disjoint sets of parallel mappers over different
                     source objects.
  ``min_statements`` top-level statements each body must have, the docstring
                     left out.

The ``*_jaccard`` keys keep their historical names; the measure behind
``op_jaccard``, ``call_jaccard`` and ``str_jaccard`` is the containment share
described above, which unlike a Jaccard does not punish drift.

Ships DEFAULT-OFF and emits WARNINGS, never failing the build. Enable with::

    [tool.lanorme.similarity]
    enabled = true
    # optional threshold overrides (defaults shown):
    # min_statements = 5
    # struct_ratio = 0.55
    # str_jaccard = 0.60
    # op_jaccard = 1.0
    # call_jaccard = 1.0
    # attr_jaccard = 0.10

Run:
    lanorme check . --check=similarity
"""

from __future__ import annotations

import ast
import difflib
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import is_flag_set, read_int
from lanorme.function_body import collect_local_bindings, list_body_statements
from lanorme.sources import Module, iter_parsed_modules, locate

# Files exempt from near-duplicate analysis (mirrors DRY-001): test functions
# and migrations are legitimately parallel by nature.
_EXCLUDED_FILENAMES = frozenset({"__init__.py", "conftest.py"})
_EXCLUDED_DIR_PARTS = frozenset({"alembic", "migrations"})


def _should_skip(*, relative: Path) -> bool:
    """True for vendor dirs, test files, and migration/init scaffolding.

    *relative* is the path inside the scan root. Matching its parts, not the
    absolute path's, keeps the user's filesystem above the root out of it.
    """
    if relative.name in _EXCLUDED_FILENAMES or relative.name.startswith("test_"):
        return True
    return any(part in _EXCLUDED_DIR_PARTS for part in relative.parts)


# Defaults, derived on the dev split of evals/corpora/duplication_similar/
# (the holdout split is never tuned against; docs/RULES.md carries its
# numbers). Each body must clear the statement floor (mirrors DRY-001) so
# short coincidental matches cannot fire.
DEFAULT_MIN_STATEMENTS = 5
# The reorder positives sit at 0.60: two of five statements swapped.
DEFAULT_STRUCT_RATIO = 0.55
# Parallel builders with the same shape share at most half their strings
# (column specs at 0.50); positives with an inserted statement carrying new
# strings keep at least two thirds (0.67).
DEFAULT_STR_JACCARD = 0.60
# Every dev positive keeps every operation and every called name; a flipped
# operator, a statement moved into a branch or a changed callee loses one.
DEFAULT_OP_JACCARD = 1.0
DEFAULT_CALL_JACCARD = 1.0
# Only the (near) all-disjoint case is rejected, separating parallel mappers
# that write the same keys from different source attributes (attr ~0) from a
# clone with one or two attributes renamed (attr still > 0.1).
DEFAULT_ATTR_JACCARD = 0.10

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef
# The fields of a compound statement that hold nested statements or clauses,
# walked as lines of their own rather than as part of the parent's line.
_BLOCK_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")
# A callee the function binds itself: the name is data, not an operation.
_LOCAL_CALLEE = "<local>"
# A callee that is an expression (``f()()``, ``handlers[k]()``): unnamed.
_UNNAMED_CALLEE = "<expr>"


@dataclass(frozen=True)
class _FunctionFingerprint:
    """The statement lines and anchor multisets of a function."""

    name: str
    node: _FuncDef
    lines: tuple[str, ...]
    operations: Counter[str]
    calls: Counter[str]
    strs: Counter[str]
    attrs: Counter[str]
    is_excluded: bool


class _StatementTokens(ast.NodeVisitor):
    """Tokenise one statement's own expressions, not its nested statements.

    Names, attribute names, numbers and strings collapse to placeholders so a
    renamed or renumbered clone still aligns; operator kinds and call arities
    stay, so the line says what the statement does. The visitor also collects
    the statement's operators, called names, strings and attributes.
    """

    def __init__(self, *, local_names: frozenset[str], skip_str_ids: set[int]) -> None:
        self.tokens: list[str] = []
        self.operators: list[str] = []
        self.calls: list[str] = []
        self.strs: Counter[str] = Counter()
        self.attrs: Counter[str] = Counter()
        self._local_names = local_names
        self._skip_str_ids = skip_str_ids

    def generic_visit(self, node: ast.AST) -> None:
        for field_name, value in ast.iter_fields(node):
            if field_name in _BLOCK_FIELDS:
                continue
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        self.visit(item)
            elif isinstance(value, ast.AST):
                self.visit(value)

    def add_operator(self, *, token: str) -> None:
        """Record an operator kind in both the line and the operation set."""
        self.tokens.append(token)
        self.operators.append(token)

    def visit_BinOp(self, node: ast.BinOp) -> None:  # noqa: N802
        self.add_operator(token=f"bin:{type(node.op).__name__}")
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:  # noqa: N802
        self.add_operator(token=f"un:{type(node.op).__name__}")
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:  # noqa: N802
        self.add_operator(token=f"boo:{type(node.op).__name__}")
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:  # noqa: N802
        for op in node.ops:
            self.add_operator(token=f"cmp:{type(op).__name__}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self.calls.append(self._name_callee(node=node))
        self.tokens.append(f"call{len(node.args)}")
        self.generic_visit(node)

    def _name_callee(self, *, node: ast.Call) -> str:
        """The called name: a method's attribute, a fixed bare name, or a placeholder."""
        target = node.func
        if isinstance(target, ast.Attribute):
            return f".{target.attr}"
        if isinstance(target, ast.Name):
            return _LOCAL_CALLEE if target.id in self._local_names else target.id
        return _UNNAMED_CALLEE

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        self.tokens.append("v")

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        # Drop the attribute name from the line; recurse into the value so the
        # access chain shape is preserved but the name is not.
        self.tokens.append("attr")
        self.attrs[node.attr] += 1
        self.visit(node.value)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        value = node.value
        if isinstance(value, bool):
            self.tokens.append("B")
        elif isinstance(value, (int, float, complex)):
            self.tokens.append("N")
        elif isinstance(value, str):
            self.tokens.append("S")
            if id(node) not in self._skip_str_ids:
                self.strs[value] += 1
        else:
            self.tokens.append("C")


# Logging / print methods whose string arguments are incidental message text,
# not meaning-bearing content. Their drift (a reworded log line) must not block
# a real clone, so these string arguments are excluded from the ``strs`` anchor.
_LOG_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"},
)


def _collect_logging_string_arg_ids(*, func: _FuncDef) -> set[int]:
    """``id()`` of str-literal nodes passed positionally to a logging/print call."""
    skip: set[int] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        is_log = (isinstance(target, ast.Attribute) and target.attr in _LOG_METHODS) or (
            isinstance(target, ast.Name) and target.id == "print"
        )
        if not is_log:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                skip.add(id(arg))
    return skip


def _iter_statements(*, body: list[ast.stmt], depth: int) -> Iterator[tuple[int, ast.AST]]:
    """Yield ``(depth, node)`` for every statement and clause under *body*, in order.

    A compound statement yields itself, then its nested blocks one level
    deeper; an ``except`` handler or a ``match`` case is a clause of its own at
    that deeper level, with its body one level deeper still.
    """
    for statement in body:
        yield depth, statement
        for field_name in _BLOCK_FIELDS:
            block = getattr(statement, field_name, None)
            if not isinstance(block, list):
                continue
            for item in block:
                if isinstance(item, ast.stmt):
                    yield from _iter_statements(body=[item], depth=depth + 1)
                elif isinstance(item, (ast.ExceptHandler, ast.match_case)):
                    yield depth + 1, item
                    yield from _iter_statements(body=item.body, depth=depth + 2)


def _is_excluded(*, func: _FuncDef) -> bool:
    """True for equality/dunder/property boilerplate that must never flag.

    Excludes a function if it is a dunder (``__x__``), is decorated with
    ``@property``, or returns a bare ``NotImplemented`` (an ``__eq__``-style
    helper). These shapes are parallel by nature and the compared
    fields/identifiers carry the meaning, not the structure.
    """
    if func.name.startswith("__") and func.name.endswith("__"):
        return True
    for decorator in func.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "property":
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr in {"getter", "setter"}:
            return True
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Name)
            and node.value.id == "NotImplemented"
        ):
            return True
    return False


def _build_fingerprint(*, func: _FuncDef) -> _FunctionFingerprint:
    """Build the statement lines and anchor multisets for one function."""
    local_names = collect_local_bindings(func=func)
    skip_str_ids = _collect_logging_string_arg_ids(func=func)
    lines: list[str] = []
    operations: Counter[str] = Counter()
    calls: Counter[str] = Counter()
    strs: Counter[str] = Counter()
    attrs: Counter[str] = Counter()
    body = list_body_statements(func=func)
    for depth, statement in _iter_statements(body=body, depth=0):
        visitor = _StatementTokens(local_names=local_names, skip_str_ids=skip_str_ids)
        if isinstance(statement, ast.AugAssign):
            visitor.add_operator(token=f"aug:{type(statement.op).__name__}")
        visitor.generic_visit(statement)
        kind = f"{depth}:{type(statement).__name__}"
        lines.append(f"{kind}:{' '.join(visitor.tokens)}")
        operations[f"{kind}:{' '.join(visitor.operators)}"] += 1
        calls.update(visitor.calls)
        strs.update(visitor.strs)
        attrs.update(visitor.attrs)
    return _FunctionFingerprint(
        name=func.name,
        node=func,
        lines=tuple(lines),
        operations=operations,
        calls=calls,
        strs=strs,
        attrs=attrs,
        is_excluded=_is_excluded(func=func),
    )


def _measure_containment(*, left: Counter[str], right: Counter[str]) -> float:
    """The share of the smaller multiset that the larger one also carries.

    ``sum(min) / min(|left|, |right|)``: 1.0 when everything the smaller side
    holds appears on the other side, whatever else the larger side adds. An
    empty side has nothing to contradict, so the share is 1.0. A statement
    inserted by drift can only add to the larger side, so it never lowers the
    share; a changed operator, callee or key replaces an element on both sides
    and does.
    """
    if not left or not right:
        return 1.0
    intersection = sum((left & right).values())
    return intersection / min(sum(left.values()), sum(right.values()))


def _measure_weighted_jaccard(*, left: Counter[str], right: Counter[str]) -> float:
    """Weighted Jaccard over two multisets: ``sum(min) / sum(max)``.

    Both empty, or one empty, is 1.0: the gate that uses it (attributes)
    exists to reject near-disjoint sets, and a side that touches no attribute
    has none to be disjoint with.
    """
    if not left or not right:
        return 1.0
    intersection = sum((left & right).values())
    union = sum((left | right).values())
    return intersection / union if union else 1.0


_THRESHOLD_KEYS = ("struct_ratio", "str_jaccard", "op_jaccard", "call_jaccard", "attr_jaccard")


def _read_ratio_setting(*, settings: dict[str, object], key: str, default: float) -> float:
    """A threshold in ``[0, 1]``; an int or a float, never a bool or a string."""
    value = settings.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"'{key}' must be a number, got {type(value).__name__}")
    return float(value)


@dataclass(frozen=True)
class _Thresholds:
    """The five gate thresholds used to decide a near-duplicate pair."""

    struct_ratio: float
    str_jaccard: float
    op_jaccard: float
    call_jaccard: float
    attr_jaccard: float


def _pair_matches(
    *,
    left: _FunctionFingerprint,
    right: _FunctionFingerprint,
    thresholds: _Thresholds,
) -> bool:
    """True when the pair clears every gate and neither side is excluded."""
    if left.is_excluded or right.is_excluded:
        return False
    # Require at least one meaning-bearing anchor across the pair. If neither
    # function carries any string OR any call, the only remaining content is
    # attribute names and numbers, both of which the lines abstract away, so a
    # perfect match here is indistinguishable from boilerplate that varies
    # purely by attribute name (state-machine guards, enum dispatch tables)
    # where those names carry the whole rule.
    if not (left.strs or right.strs or left.calls or right.calls):
        return False
    # The set gates are cheap and reject most pairs; the sequence match, the
    # expensive gate, runs last and only after its upper bounds clear the bar.
    if _measure_containment(left=left.strs, right=right.strs) < thresholds.str_jaccard:
        return False
    op_share = _measure_containment(left=left.operations, right=right.operations)
    if op_share < thresholds.op_jaccard:
        return False
    if _measure_containment(left=left.calls, right=right.calls) < thresholds.call_jaccard:
        return False
    if _measure_weighted_jaccard(left=left.attrs, right=right.attrs) < thresholds.attr_jaccard:
        return False
    matcher = difflib.SequenceMatcher(None, left.lines, right.lines)
    if matcher.real_quick_ratio() < thresholds.struct_ratio:
        return False
    if matcher.quick_ratio() < thresholds.struct_ratio:
        return False
    return matcher.ratio() >= thresholds.struct_ratio


def _collect_fingerprints(*, module: Module, min_statements: int) -> list[_FunctionFingerprint]:
    """Fingerprint every function (incl. methods and nested) clearing the floor."""
    prints: list[_FunctionFingerprint] = []
    for node in module.index.functions:
        if len(list_body_statements(func=node)) >= min_statements:
            prints.append(_build_fingerprint(func=node))
    return prints


def _scan_file(
    *,
    module: Module,
    min_statements: int,
    thresholds: _Thresholds,
) -> list[Violation]:
    """Pair the qualifying functions WITHIN one file and warn on near-dupes."""
    prints = _collect_fingerprints(module=module, min_statements=min_statements)
    warnings: list[Violation] = []
    for left, right in combinations(prints, 2):
        if _pair_matches(left=left, right=right, thresholds=thresholds):
            first, second = sorted((left, right), key=lambda fp: fp.node.lineno)
            warnings.append(
                Violation(
                    file=module.relative,
                    line=first.node.lineno,
                    rule="SIMILAR-001",
                    message=(
                        f"Functions '{first.name}' and '{second.name}' are near-duplicates: "
                        f"the same operations in the same positions, agreeing on their "
                        f"strings and called names, differing only in names, numbers and "
                        f"one or two statements"
                    ),
                    fix="Extract the shared logic into a common helper function",
                    **locate(first.node),
                ),
            )
    return warnings


@dataclass
class SimilarityCheck:
    """SIMILAR-001: structural near-duplicate detection (advisory, default-off)."""

    name: str = "similarity"
    description: str = "Structural near-duplicate detection (SIMILAR-001, advisory)"
    enabled: bool = False
    min_statements: int = DEFAULT_MIN_STATEMENTS
    struct_ratio: float = DEFAULT_STRUCT_RATIO
    str_jaccard: float = DEFAULT_STR_JACCARD
    op_jaccard: float = DEFAULT_OP_JACCARD
    call_jaccard: float = DEFAULT_CALL_JACCARD
    attr_jaccard: float = DEFAULT_ATTR_JACCARD
    rules: list[str] = field(
        default_factory=lambda: [
            "SIMILAR-001: Two functions carry out the same operations in the same "
            "control-flow positions and agree on their string literals and called names, "
            "differing only in variable, attribute and keyword names, numbers, and one or "
            "two inserted, removed or reordered statements, so they should likely share a "
            "helper (advisory; default-off)",
        ],
    )
    settings_keys: ClassVar[frozenset[str]] = frozenset(
        {"enabled", "min_statements", *_THRESHOLD_KEYS},
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.similarity]`` configuration."""
        self.enabled = is_flag_set(settings=settings, key="enabled", default=self.enabled)
        self.min_statements = read_int(
            settings=settings,
            key="min_statements",
            default=self.min_statements,
        )
        for key in _THRESHOLD_KEYS:
            current: float = getattr(self, key)
            setattr(self, key, _read_ratio_setting(settings=settings, key=key, default=current))

    def _build_thresholds(self) -> _Thresholds:
        return _Thresholds(
            struct_ratio=self.struct_ratio,
            str_jaccard=self.str_jaccard,
            op_jaccard=self.op_jaccard,
            call_jaccard=self.call_jaccard,
            attr_jaccard=self.attr_jaccard,
        )

    def run(self, *, src_root: str) -> CheckResult:
        """Scan files under *src_root*; emit SIMILAR-001 warnings, never failing."""
        if not self.enabled:
            return CheckResult.from_findings(check=self.name)
        warnings: list[Violation] = []
        thresholds = self._build_thresholds()
        for module in iter_parsed_modules(Path(src_root)):
            if _should_skip(relative=Path(module.relative)):
                continue
            # Per-file isolation: a single pathological file (a deeply nested
            # body that overflows the recursive walk) must never abort the
            # whole advisory run.
            try:
                warnings.extend(
                    _scan_file(
                        module=module,
                        min_statements=self.min_statements,
                        thresholds=thresholds,
                    ),
                )
            except RecursionError:
                continue
        return CheckResult.from_findings(check=self.name, warnings=warnings)


# Self-register on import.
register(SimilarityCheck())
