"""NAMING-005: a name must be as long as the distance a reader carries it.

``i`` in a three-line loop is perfectly readable. The same ``i`` bound at the
top of a sixty-line function and used at the bottom is not, because by the time
you reach the use, the binding has scrolled out of sight and the name itself
has to carry the meaning. The defect is not shortness, it is shortness held
over distance, so the rule scales the requirement with the span rather than
banning short names outright.

Span is measured from where a name is first bound to where it is last
referenced, inside one function. That is the window a reader must hold it in.

Calibration (see ``evals/corpora/naming_scope/``). Short-name spans measured
over LaNorme's own ``src/`` and the 18 generated modules (about 10,000
lines) under ``evals/``:

    src/                  p95 = 10, max = 18, nothing beyond 20
    generated code        p90 = 21, max = 53

The default ``max_span`` of 20 sits in that gap, and has a reason beyond the
gap: twenty lines is roughly a screenful, so it is the point at which a binding
and its use stop being visible together. At that default LaNorme's own source
is clean and the generated corpus yields findings such as ``rc`` held over 53
lines and ``s`` over 52.

Default-off (opinionated). Opt in via::

    [tool.lanorme.naming_scope]
    enabled = true

Run:
    lanorme check . --check=naming_scope
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import read_int, is_flag_set, read_str_list
from lanorme.scan import Scan
from lanorme.sources import iter_parsed_modules, locate

# Beyond this many lines between binding and last use, a short name stops
# paying for itself. Roughly one screen: see the calibration above.
DEFAULT_MAX_SPAN = 20

# Names at or below this length, underscores stripped, are "short".
DEFAULT_MAX_SHORT_LENGTH = 2

# Short names that stay readable at any distance because the idiom carries the
# meaning: loop counters, throwaway targets, maths axes, and a few two-letter
# conventions. Projects extend this through the ``allow`` setting rather than
# raising the span, so the exemption stays visible in config.
DEFAULT_ALLOW = frozenset(
    {
        "_",
        "i",
        "j",
        "k",
        "n",
        "x",
        "y",
        "z",
        "db",
        "id",
        "fd",
        "fh",
        "ok",
        "lo",
        "hi",
        "lr",
        "ax",
        "df",
        "ts",
    },
)

_SKIP_DIRS = frozenset({"alembic", "migrations"})


@dataclass
class _Extent:
    """Where a name is first bound and last referenced, in line numbers.

    ``node`` is the earliest binding or reference seen, where the finding is placed.
    """

    first: int
    last: int
    node: ast.AST

    @property
    def span(self) -> int:
        """Lines a reader must carry the name across, inclusive."""
        return self.last - self.first + 1

    def extend(self, *, line: int, node: ast.AST) -> None:
        """Widen the extent to *line*; an earlier line moves the anchor to *node*."""
        if line < self.first:
            self.first, self.node = line, node
        self.last = max(self.last, line)


def _is_short(*, name: str, max_short_length: int, allow: frozenset[str]) -> bool:
    """True if *name* is too short to carry meaning on its own."""
    if name in allow or name.startswith("__"):
        return False
    return len(name.lstrip("_")) <= max_short_length


_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _collect_own_bindings(*, scope: ast.AST) -> set[str]:
    """Names *scope* binds itself: parameters, comprehension targets, stores, captures.

    Bindings inside a nested scope are that scope's own and are not included.
    """
    bound: set[str] = set()
    pending: list[ast.AST] = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        if isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
            bound.add(node.name)
        if isinstance(node, _SCOPES):
            # A comprehension's iterable and a lambda's defaults are evaluated
            # outside it; its arguments and targets are its own.
            pending.extend(_list_outer_parts(scope=node))
            continue
        pending.extend(ast.iter_child_nodes(node))
    return bound


def _list_outer_parts(*, scope: ast.AST) -> list[ast.AST]:
    """The children of a nested scope that belong to the enclosing one."""
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [*scope.decorator_list, *_list_defaults(args=scope.args)]
    if isinstance(scope, ast.Lambda):
        return _list_defaults(args=scope.args)
    return [scope.generators[0].iter] if scope.generators else []


def _list_defaults(*, args: ast.arguments) -> list[ast.AST]:
    """Default expressions of *args*; a keyword-only argument without one holds ``None``."""
    return [node for node in (*args.defaults, *args.kw_defaults) if node is not None]


def _iter_name_uses(*, scope: ast.AST, shadowed: frozenset[str]) -> Iterator[tuple[str, ast.AST]]:
    """Every name mention in *scope* that refers to the enclosing function's binding.

    A nested function, lambda or comprehension is walked too, but a name it
    binds itself (*shadowed*) is its own and is skipped, so an inner ``s`` does
    not stretch the outer ``s``.
    """
    pending: list[tuple[ast.AST, frozenset[str]]] = [
        (child, shadowed) for child in ast.iter_child_nodes(scope)
    ]
    while pending:
        node, hidden = pending.pop()
        if isinstance(node, ast.arg):
            if node.arg not in hidden:
                yield node.arg, node
        elif isinstance(node, ast.Name):
            if node.id not in hidden:
                yield node.id, node
        elif (
            isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name and node.name not in hidden
        ):
            yield node.name, node
        if isinstance(node, _SCOPES):
            inner = hidden | _collect_own_bindings(scope=node)
            pending.extend((child, inner) for child in ast.iter_child_nodes(node))
            continue
        pending.extend((child, hidden) for child in ast.iter_child_nodes(node))


def _collect_local_extents(*, func: ast.AST) -> dict[str, _Extent]:
    """Line extent of every name *func* binds, keyed by name.

    Only names the function itself binds are tracked: parameters, assignment
    targets and ``match`` captures. A referenced-but-not-bound name is a module
    import or a global (``np``, ``re``), where the short name is the library's
    choice and not this function's to make. A name a nested function, lambda
    or comprehension binds is that scope's own and does not count.
    """
    bound = _collect_own_bindings(scope=func)
    extents: dict[str, _Extent] = {}
    for name, node in _iter_name_uses(scope=func, shadowed=frozenset()):
        if name not in bound:
            continue
        line = getattr(node, "lineno", None)
        if line is None:
            continue
        seen = extents.get(name)
        if seen is None:
            extents[name] = _Extent(first=line, last=line, node=node)
        else:
            seen.extend(line=line, node=node)
    return extents


def _collect_function_violations(
    *,
    func: ast.AST,
    file: str,
    settings: _Settings,
) -> list[Violation]:
    """Flag every short name in *func* held over more than the allowed span."""
    violations: list[Violation] = []
    for name, extent in sorted(_collect_local_extents(func=func).items()):
        short = _is_short(
            name=name,
            max_short_length=settings.max_short_length,
            allow=settings.allow,
        )
        if not short or extent.span <= settings.max_span:
            continue
        violations.append(
            Violation(
                file=file,
                line=extent.first,
                rule="NAMING-005: A short name must not be carried across a long span",
                message=(
                    f"Name '{name}' is bound here and still in use {extent.span} lines later "
                    f"in '{getattr(func, 'name', '?')}' (limit: {settings.max_span})"
                ),
                fix="Give it a name that reads at the point of use, or shorten the span it lives across",
                **locate(extent.node),
            ),
        )
    return violations


@dataclass(frozen=True)
class _Settings:
    """Resolved thresholds for one run."""

    max_span: int
    max_short_length: int
    allow: frozenset[str]


@dataclass
class NamingScopeCheck:
    """NAMING-005: short names must not be carried across long spans (opt-in)."""

    name: str = "naming_scope"
    description: str = "Short names held across a long span (NAMING-005)"
    enabled: bool = False
    max_span: int = DEFAULT_MAX_SPAN
    max_short_length: int = DEFAULT_MAX_SHORT_LENGTH
    allow: frozenset[str] = DEFAULT_ALLOW
    rules: list[str] = field(
        default_factory=lambda: [
            "NAMING-005: A short name must not be carried across a long span",
        ],
    )
    settings_keys: ClassVar[frozenset[str]] = frozenset(
        {"enabled", "max_span", "max_short_length", "allow"},
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.naming_scope]`` configuration."""
        self.enabled = is_flag_set(settings=settings, key="enabled", default=self.enabled)
        self.max_span = read_int(settings=settings, key="max_span", default=self.max_span)
        self.max_short_length = read_int(
            settings=settings,
            key="max_short_length",
            default=self.max_short_length,
        )
        if "allow" in settings:
            extra = read_str_list(settings=settings, key="allow")
            self.allow = DEFAULT_ALLOW | frozenset(extra)

    def check(self, scan: Scan) -> CheckResult:
        """Walk every Python file and collect NAMING-005 violations."""
        if not self.enabled:
            return CheckResult.from_findings(check=self.name)
        resolved = _Settings(
            max_span=self.max_span,
            max_short_length=self.max_short_length,
            allow=frozenset(self.allow),
        )
        violations: list[Violation] = []
        for module in iter_parsed_modules(scan.root):
            # Match skip directories inside the root only: the absolute path's
            # ancestors are the user's filesystem, not the project layout.
            if any(
                part in _SKIP_DIRS for part in module.relative.split("/")
            ) or module.path.name.startswith("test_"):
                continue
            file = module.relative
            for node in module.index.functions:
                violations.extend(
                    _collect_function_violations(func=node, file=file, settings=resolved),
                )
        return CheckResult.from_findings(check=self.name, violations=violations)


register(NamingScopeCheck())
