"""SUPPRESS-001 / SUPPRESS-002: keep the escape hatches visible and countable.

Every other rule in LaNorme can be switched off on a line with ``# noqa`` or
``# lanorme: ignore[...]``. That is deliberate, and it is also why adding rules
raises a project's ceiling without moving its floor: a rule one comment away
from off is a suggestion, not a standard.

These two rules do not close the hatches. They price them.

    SUPPRESS-001  the project's inline suppression count against a budget
    SUPPRESS-002  blanket directives, which silence rules nobody has written yet

A bare ``# noqa`` is worse than ``# noqa: TYPE-001`` in a way that compounds:
it silences every current rule on the line and every future one too, so a line
suppressed in 2024 quietly opts out of everything added since. SUPPRESS-002
flags those regardless of budget.

Used as a ratchet, set ``max_total`` to today's count and lower it as debt is
paid; CI then fails on the next suppression added rather than on the backlog.

Comments are read through ``tokenize``, so a directive named inside a string or
a docstring (this module's own prose, for instance) is not counted.

**These codes cannot be silenced inline.** A budget an offender can waive on
the offending line is not a budget, so ``lanorme.filters`` refuses inline
directives for the ``SUPPRESS`` category. They remain switchable in config,
which is the point: an escape belongs in a reviewed file, not scattered
invisibly across source lines.

Default-off (opinionated). Opt in via::

    [tool.lanorme.suppressions]
    enabled   = true
    max_total = 0

Run:
    lanorme check . --check=suppressions
"""

from __future__ import annotations

import io
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import read_int, is_flag_set
from lanorme.filters import _IGNORE_RE, _NOQA_RE
from lanorme.sources import iter_parsed_modules

# Code lists that name no rule in particular, so the directive covers whatever
# exists now and whatever lands later.
_BLANKET_CODES = frozenset({"ALL", "*"})


@dataclass(frozen=True)
class _Directive:
    """One suppression comment: where it is, and whether it names any rule."""

    file: str
    line: int
    column: int
    text: str
    blanket: bool


def _classify(*, comment: str) -> bool | None:
    """True if *comment* is a blanket directive, False if targeted, None if neither.

    Anchored at the start of the comment token, not searched within it. Prose
    that names a directive (``# they line up with --exclude / # noqa.``) is
    documentation, not an escape, and counting it would inflate the budget with
    the very comments that explain the feature.
    """
    for pattern in (_NOQA_RE, _IGNORE_RE):
        match = pattern.match(comment)
        if match is None:
            continue
        if match.group(1) is None:
            return True
        codes = {c.strip().upper() for c in match.group(1).split(",") if c.strip()}
        return bool(codes & _BLANKET_CODES)
    return None


def _collect_directives(*, source: str, relative: str) -> list[_Directive]:
    """Every suppression directive in one file, read from comment tokens only."""
    found: list[_Directive] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):
        return found
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        blanket = _classify(comment=token.string)
        if blanket is None:
            continue
        found.append(
            _Directive(
                file=relative,
                line=token.start[0],
                column=token.start[1],
                text=token.string.strip(),
                blanket=blanket,
            ),
        )
    return found


def _find_budget_violation(*, directives: list[_Directive], max_total: int) -> list[Violation]:
    """SUPPRESS-001: one finding when the project is over its suppression budget."""
    if len(directives) <= max_total:
        return []
    anchor = directives[0]
    per_file: dict[str, int] = {}
    for directive in directives:
        per_file[directive.file] = per_file.get(directive.file, 0) + 1
    worst = sorted(per_file.items(), key=lambda item: (-item[1], item[0]))[:3]
    summary = ", ".join(f"{name} ({count})" for name, count in worst)
    return [
        Violation(
            file=anchor.file,
            line=anchor.line,
            column=anchor.column,
            rule="SUPPRESS-001: Inline suppressions must stay within the project's budget",
            message=(
                f"{len(directives)} inline suppressions across {len(per_file)} files "
                f"(budget: {max_total}). Most suppressed: {summary}"
            ),
            fix="Fix the findings, or raise max_total deliberately so the debt is recorded in config",
        ),
    ]


def _find_blanket_violations(*, directives: list[_Directive]) -> list[Violation]:
    """SUPPRESS-002: one finding per directive that names no rule."""
    return [
        Violation(
            file=directive.file,
            line=directive.line,
            column=directive.column,
            rule="SUPPRESS-002: A suppression must name the rule it silences",
            message=f"Blanket directive '{directive.text}' silences every rule, including future ones",
            fix="Name the codes it needs: '# noqa: TYPE-001' or '# lanorme: ignore[TYPE-001]'",
        )
        for directive in directives
        if directive.blanket
    ]


@dataclass
class SuppressionsCheck:
    """SUPPRESS-001 / SUPPRESS-002: inline suppressions stay budgeted and specific (opt-in)."""

    name: str = "suppressions"
    description: str = (
        "Inline suppression budget and blanket directives (SUPPRESS-001, SUPPRESS-002)"
    )
    enabled: bool = False
    max_total: int = 0
    allow_blanket: bool = False
    rules: list[str] = field(
        default_factory=lambda: [
            "SUPPRESS-001: Inline suppressions must stay within the project's budget",
            "SUPPRESS-002: A suppression must name the rule it silences",
        ],
    )
    settings_keys: ClassVar[frozenset[str]] = frozenset({"enabled", "max_total", "allow_blanket"})

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.suppressions]`` configuration."""
        self.enabled = is_flag_set(settings=settings, key="enabled", default=self.enabled)
        self.max_total = read_int(settings=settings, key="max_total", default=self.max_total)
        self.allow_blanket = is_flag_set(
            settings=settings,
            key="allow_blanket",
            default=self.allow_blanket,
        )

    def run(self, *, src_root: str) -> CheckResult:
        """Collect every suppression directive, then price it."""
        if not self.enabled:
            return CheckResult.from_findings(check=self.name)
        directives: list[_Directive] = []
        for module in iter_parsed_modules(Path(src_root)):
            directives.extend(_collect_directives(source=module.source, relative=module.relative))
        directives.sort(key=lambda d: (d.file, d.line))

        violations = _find_budget_violation(directives=directives, max_total=self.max_total)
        if not self.allow_blanket:
            violations.extend(_find_blanket_violations(directives=directives))
        return CheckResult.from_findings(check=self.name, violations=violations)


register(SuppressionsCheck())
