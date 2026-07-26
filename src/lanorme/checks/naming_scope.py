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
over LaNorme's own ``src/`` and the 3321 lines of generated code under
``evals/``:

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
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.discovery import iter_py_files

# Beyond this many lines between binding and last use, a short name stops
# paying for itself. Roughly one screen: see the calibration above.
DEFAULT_MAX_SPAN = 20

# Names at or below this length, underscores stripped, are "short".
DEFAULT_MAX_SHORT_LENGTH = 2

# Short names that stay readable at any distance because the idiom carries the
# meaning: loop counters, throwaway targets, maths axes, and a few two-letter
# conventions. Projects extend this through the ``allow`` setting rather than
# raising the span, so the exemption stays visible in config.
DEFAULT_ALLOW = frozenset({
    "_", "i", "j", "k", "n", "x", "y", "z",
    "db", "id", "fd", "fh", "ok", "lo", "hi", "lr", "ax", "df", "ts",
})

_SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", "alembic", "migrations"}
)
_FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass
class _Extent:
    """Where a name is first bound and last referenced, in line numbers."""

    first: int
    last: int

    @property
    def span(self) -> int:
        """Lines a reader must carry the name across, inclusive."""
        return self.last - self.first + 1


def _is_short(*, name: str, max_short_length: int, allow: frozenset[str]) -> bool:
    """True if *name* is too short to carry meaning on its own."""
    if name in allow or name.startswith("__"):
        return False
    return len(name.lstrip("_")) <= max_short_length


def _local_extents(*, func: ast.AST) -> dict[str, _Extent]:
    """Line extent of every name *func* binds, keyed by name.

    Only names the function itself binds are tracked: parameters and assignment
    targets. A referenced-but-not-bound name is a module import or a global
    (``np``, ``re``), where the short name is the library's choice and not this
    function's to make.
    """
    bound: set[str] = set()
    extents: dict[str, _Extent] = {}
    for node in ast.walk(func):
        name = None
        if isinstance(node, ast.arg):
            name = node.arg
            bound.add(name)
        elif isinstance(node, ast.Name):
            name = node.id
            if isinstance(node.ctx, ast.Store):
                bound.add(name)
        if name is None:
            continue
        line = getattr(node, "lineno", None)
        if line is None:
            continue
        seen = extents.get(name)
        extents[name] = _Extent(first=min(seen.first, line), last=max(seen.last, line)) if seen else _Extent(first=line, last=line)
    return {name: extent for name, extent in extents.items() if name in bound}


def _function_violations(*, func: ast.AST, file: str, settings: _Settings) -> list[Violation]:
    """Flag every short name in *func* held over more than the allowed span."""
    violations: list[Violation] = []
    for name, extent in sorted(_local_extents(func=func).items()):
        short = _is_short(name=name, max_short_length=settings.max_short_length, allow=settings.allow)
        if not short or extent.span <= settings.max_span:
            continue
        violations.append(Violation(
            file=file,
            line=extent.first,
            rule="NAMING-005: A short name must not be carried across a long span",
            message=(
                f"Name '{name}' is bound here and still in use {extent.span} lines later "
                f"in '{getattr(func, 'name', '?')}' (limit: {settings.max_span})"
            ),
            fix="Give it a name that reads at the point of use, or shorten the span it lives across",
        ))
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
        ]
    )

    def configure(self, *, settings: dict[str, bool | int | list[str]]) -> None:
        """Apply ``[tool.lanorme.naming_scope]`` configuration."""
        if "enabled" in settings:
            self.enabled = bool(settings["enabled"])
        if "max_span" in settings:
            self.max_span = int(settings["max_span"])
        if "max_short_length" in settings:
            self.max_short_length = int(settings["max_short_length"])
        extra = settings.get("allow")
        if isinstance(extra, list):
            self.allow = DEFAULT_ALLOW | {str(item) for item in extra}

    def run(self, *, src_root: str) -> CheckResult:
        """Walk every Python file and collect NAMING-005 violations."""
        if not self.enabled:
            return CheckResult(check=self.name, status=Status.PASS, violations=[])
        resolved = _Settings(
            max_span=self.max_span,
            max_short_length=self.max_short_length,
            allow=frozenset(self.allow),
        )
        violations: list[Violation] = []
        root = Path(src_root)
        for path in iter_py_files(root):
            if any(part in _SKIP_DIRS for part in path.parts) or path.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            relative = str(path.relative_to(root))
            for node in ast.walk(tree):
                if isinstance(node, _FUNCTION_TYPES):
                    violations.extend(_function_violations(func=node, file=relative, settings=resolved))
        status = Status.FAIL if violations else Status.PASS
        return CheckResult(check=self.name, status=status, violations=violations)


register(NamingScopeCheck())
