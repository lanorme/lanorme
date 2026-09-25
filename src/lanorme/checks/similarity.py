"""SIMILAR-001: structural near-duplicate detection (advisory, default-off).

A precision-first companion to DRY-001. DRY-001 catches EXACT structural
clones (identical normalised AST modulo variable/function names and string
literals) and fails the build. It keeps attribute names and string literals in
its dump and so misses anything off by one token: a changed number, a renamed
attribute, an added statement, a reordering.

SIMILAR-001 fills that gap. It compares the two (or more) qualifying functions
in a file pairwise on two signals:

  Signal A (structure): a token sequence over the function body that ABSTRACTS
  away local variable names, attribute names and numeric literals, so an
  attribute-renamed or number-changed clone still aligns. Similarity is
  ``difflib.SequenceMatcher.ratio()``, whose block alignment tolerates one or
  two added, removed or reordered statements.

  Signal B (semantic anchors): three multisets that DRY-001 throws away but
  that carry meaning - string-literal VALUES, called NAMES (bare-call ids and
  method attribute names), and operator KINDS. Agreement is a weighted Jaccard
  over each multiset.

A pair is flagged only when the structure is highly similar AND all three
anchor signals agree above their thresholds AND the pair is not
equality/dunder/property boilerplate. Keeping strings, calls and operators is
the precision guard that separates real clones from legitimately parallel
boilerplate (config builders, dispatch tables, field mappers, framework
handlers) whose shape is identical but whose identifiers carry the meaning.

Ships DEFAULT-OFF and emits WARNINGS, never failing the build. Enable with::

    [tool.lanorme.similarity]
    enabled = true
    # optional threshold overrides (defaults shown):
    # min_statements = 5
    # struct_ratio = 0.55
    # str_jaccard = 0.60
    # op_jaccard = 0.60
    # call_jaccard = 0.35
    # attr_jaccard = 0.10

Run:
    lanorme check . --check=similarity
"""

from __future__ import annotations

import ast
import difflib
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import read_int, is_flag_set
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


# Defaults. Each function body must clear this floor (mirrors DRY-001) so short
# coincidental matches cannot fire.
DEFAULT_MIN_STATEMENTS = 5

# Validated operating point (corpus: precision 1.0, recall 0.85). A pair flags
# only when structure is similar AND every anchor agrees. str is the precision
# backbone (parallel builders share shape but their string keys differ); op is
# floored just above the operator-divergence negatives (clamp/sum-vs-product
# sit at 0.5).
DEFAULT_STRUCT_RATIO = 0.55
DEFAULT_STR_JACCARD = 0.60
DEFAULT_OP_JACCARD = 0.60
# Loose: no negative in the corpus is separated by the call anchor alone, so it
# is floored only enough to admit method-renamed clones (a legitimate edit).
DEFAULT_CALL_JACCARD = 0.35
# Loose floor: only the (near) all-disjoint case is rejected, separating
# parallel mappers that write the same keys from different source attributes
# (attr ~0) from a clone with one or two attributes renamed (attr still > 0.1).
DEFAULT_ATTR_JACCARD = 0.10

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class _FunctionFingerprint:
    """The structural token sequence and three anchor multisets of a function."""

    name: str
    node: _FuncDef
    struct: tuple[str, ...]
    calls: Counter[str]
    strs: Counter[str]
    ops: Counter[str]
    attrs: Counter[str]
    is_excluded: bool


class _StructVisitor(ast.NodeVisitor):
    """Emit a token sequence abstracting away var/attr names and numbers.

    Statement markers, operator markers, call arity markers and leaf
    placeholders are appended in source order. Attribute names and numeric
    literals are deliberately dropped so attribute-renamed and number-changed
    clones still align.
    """

    def __init__(self) -> None:
        self.tokens: list[str] = []

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self.tokens.append("ASG")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        self.tokens.append("ASG")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        self.tokens.append(f"AUG:{type(node.op).__name__}")
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        self.tokens.append("IF")
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        self.tokens.append("FOR")
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        self.tokens.append("WHL")
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
        self.tokens.append("RET")
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:  # noqa: N802
        self.tokens.append("RAI")
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:  # noqa: N802
        self.tokens.append("EXP")
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:  # noqa: N802
        self.tokens.append(f"bin:{type(node.op).__name__}")
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:  # noqa: N802
        self.tokens.append(f"un:{type(node.op).__name__}")
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:  # noqa: N802
        for op in node.ops:
            self.tokens.append(f"cmp:{type(op).__name__}")
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:  # noqa: N802
        self.tokens.append(f"boo:{type(node.op).__name__}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self.tokens.append(f"call{len(node.args)}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        self.tokens.append("v")

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        # Drop the attribute name; recurse into the value so the access chain
        # shape is preserved but the name is not.
        self.tokens.append("attr")
        self.visit(node.value)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        value = node.value
        if isinstance(value, bool):
            self.tokens.append("B")
        elif isinstance(value, (int, float)):
            self.tokens.append("N")
        elif isinstance(value, str):
            self.tokens.append("S")
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


def _list_op_names(node: ast.AST) -> list[str]:
    """Operator kind name(s) for an op-bearing node, else an empty list."""
    if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.AugAssign, ast.BoolOp)):
        return [type(node.op).__name__]
    if isinstance(node, ast.Compare):
        return [type(op).__name__ for op in node.ops]
    return []


def _call_name(node: ast.Call) -> str | None:
    """The called name: a bare-call id, or a method's attribute name."""
    target = node.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _build_anchors(
    *,
    func: _FuncDef,
) -> tuple[Counter[str], Counter[str], Counter[str], Counter[str]]:
    """Return (calls, strs, ops, attrs) multisets over the whole function body.

    ``attrs`` (accessed attribute names) is abstracted out of the structural
    channel for attribute-rename recall, but kept here as a precision anchor:
    parallel mappers writing the same dict keys from disjoint source attributes
    (mail_host vs bucket_host) are separated by it.
    """
    calls: Counter[str] = Counter()
    strs: Counter[str] = Counter()
    ops: Counter[str] = Counter()
    attrs: Counter[str] = Counter()
    skip_str_ids = _collect_logging_string_arg_ids(func=func)
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name is not None:
                calls[name] += 1
        elif isinstance(node, ast.Attribute):
            attrs[node.attr] += 1
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in skip_str_ids:
                strs[node.value] += 1
        else:
            for op in _list_op_names(node):
                ops[op] += 1
    return calls, strs, ops, attrs


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
    """Build the structural sequence and anchor multisets for one function."""
    visitor = _StructVisitor()
    for stmt in func.body:
        visitor.visit(stmt)
    calls, strs, ops, attrs = _build_anchors(func=func)
    return _FunctionFingerprint(
        name=func.name,
        node=func,
        struct=tuple(visitor.tokens),
        calls=calls,
        strs=strs,
        ops=ops,
        attrs=attrs,
        is_excluded=_is_excluded(func=func),
    )


def _measure_weighted_jaccard(
    *,
    left: Counter[str],
    right: Counter[str],
    empty_is_agreement: bool = False,
) -> float:
    """Weighted Jaccard over two multisets.

    Both empty -> 1.0 (they agree: neither carries any of this anchor).
    One empty, one not -> 0.0 by default (genuine disagreement). Otherwise the
    standard sum(min) / sum(max) ratio.

    With ``empty_is_agreement`` the one-empty case also returns 1.0. This is
    used only for the OPERATOR anchor: the op gate exists to catch operator
    DIVERGENCE (clamp-above vs clamp-below, plus vs minus), which needs both
    sides to carry operators that disagree. When one side simply has no
    operators (a removed statement was the only op-bearing one) there is no
    divergence to detect, so a copy-paste clone should not be punished here.
    """
    if not left and not right:
        return 1.0
    if not left or not right:
        return 1.0 if empty_is_agreement else 0.0
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
    """The four gate thresholds used to decide a near-duplicate pair."""

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
    # attribute names and numbers, both of which the structural channel
    # abstracts away, so a perfect match here is indistinguishable from
    # boilerplate that varies purely by attribute name (state-machine guards,
    # enum dispatch tables) where those names carry the whole rule.
    if not (left.strs or right.strs or left.calls or right.calls):
        return False
    # The set gates are cheap and reject most pairs; the sequence match, the
    # expensive gate, runs last and only after its upper bounds clear the bar.
    if _measure_weighted_jaccard(left=left.strs, right=right.strs) < thresholds.str_jaccard:
        return False
    op_jaccard = _measure_weighted_jaccard(left=left.ops, right=right.ops, empty_is_agreement=True)
    if op_jaccard < thresholds.op_jaccard:
        return False
    if _measure_weighted_jaccard(left=left.calls, right=right.calls) < thresholds.call_jaccard:
        return False
    # Accessed-attribute agreement. empty_is_agreement so functions that touch
    # no attributes are not punished; the gate only rejects pairs whose
    # attribute sets are (near) disjoint, i.e. parallel mappers over different
    # source objects.
    attr_jaccard = _measure_weighted_jaccard(
        left=left.attrs,
        right=right.attrs,
        empty_is_agreement=True,
    )
    if attr_jaccard < thresholds.attr_jaccard:
        return False
    matcher = difflib.SequenceMatcher(None, left.struct, right.struct)
    if matcher.real_quick_ratio() < thresholds.struct_ratio:
        return False
    if matcher.quick_ratio() < thresholds.struct_ratio:
        return False
    return matcher.ratio() >= thresholds.struct_ratio


def _collect_fingerprints(*, module: Module, min_statements: int) -> list[_FunctionFingerprint]:
    """Fingerprint every function (incl. methods and nested) clearing the floor."""
    prints: list[_FunctionFingerprint] = []
    for node in module.index.functions:
        if len(node.body) >= min_statements:
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
                        f"Functions '{first.name}' and '{second.name}' are structurally "
                        f"near-duplicate (same skeleton after abstracting variable/attribute "
                        f"names and numbers) and agree on their strings, calls and operators"
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
            "SIMILAR-001: Two functions are structurally near-duplicate (same skeleton "
            "after abstracting variable names, attribute names and numbers) and agree on "
            "their string literals, called names and operators, so they should likely "
            "share a helper (advisory; default-off)",
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
