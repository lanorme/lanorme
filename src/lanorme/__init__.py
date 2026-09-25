"""LaNorme, a configurable, pluggable architecture & code-quality linter.

This module is the stable public API for writing checks. A check is any object
implementing the ``Check`` protocol below; register it with ``register()`` and
LaNorme will discover and run it.

Run all checks against a path:
    lanorme check .

Run a single check:
    lanorme check . --check=layer_deps

Run a single rule code or category:
    lanorme check . --check=DRY-001

JSON output for tooling/agents:
    lanorme check . --output-format=json     # one object per check
    lanorme check . --output-format=ndjson   # one finding per line, jq-friendly

List every registered rule:
    lanorme rules
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable

__version__ = "0.20.0"


class Status(enum.Enum):
    """Result status for a check run."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Violation:
    """A single rule violation found by a check.

    ``rule`` is the code alone (``DRY-001``) or the declared rule string
    (``DRY-001: description``); the runner expands a bare code to the string
    the check declares in ``rules``, so every output carries the description.
    ``line`` is 1-based and ``0`` or ``1`` for a finding about the whole file.
    The span fields are optional: ``column`` and ``end_column`` are 0-based
    like ``ast``'s offsets, ``end_line`` is 1-based and inclusive.
    """

    file: str
    line: int
    rule: str
    message: str
    fix: str
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None
    promoted: bool = False

    @property
    def code(self) -> str:
        """The rule code (e.g. ``DRY-001``) parsed from the rule string."""
        return extract_code(self.rule)

    @property
    def scope(self) -> str:
        """``span`` when the extent is known, ``file`` for a whole-file finding, else ``line``.

        A whole-file finding is reported at line 0 or 1 with no position (the
        convention SIZE-001, PORT-001 and the baseline share); a finding that
        knows its column is about that line even when it is line 1.
        """
        if self.end_line is not None:
            return "span"
        if self.line <= 1 and self.column is None:
            return "file"
        return "line"

    def to_dict(self) -> dict[str, str | int | bool | None]:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "scope": self.scope,
            "code": self.code,
            "rule": self.rule,
            "message": self.message,
            "fix": self.fix,
            "promoted": self.promoted,
        }

    def format_human(self, *, label: str = "VIOLATION") -> str:
        """The three-line human rendering; *label* tells a violation from a warning."""
        return (
            f"  {label}: {self.file}:{self.line} — {self.message}\n"
            f"    Rule: {self.rule}\n"
            f"    Fix: {self.fix}"
        )


def extract_code(rule: str) -> str:
    """The code (``DRY-001``) at the head of a rule string, or ``""`` for an empty one."""
    head = rule.split(":", 1)[0].split()
    return head[0] if head else ""


@dataclass
class CheckResult:
    """Result of running a single check.

    Build one with :meth:`from_findings` so the status always agrees with the
    finding lists: any violation is ``FAIL``, otherwise any warning is ``WARN``,
    otherwise ``PASS``.
    """

    check: str
    status: Status
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)

    @classmethod
    def from_findings(
        cls,
        *,
        check: str,
        violations: Iterable[Violation] = (),
        warnings: Iterable[Violation] = (),
    ) -> CheckResult:
        """A result whose status is derived from its findings."""
        hard = list(violations)
        soft = list(warnings)
        status = Status.FAIL if hard else (Status.WARN if soft else Status.PASS)
        return cls(check=check, status=status, violations=hard, warnings=soft)

    def filter_findings(self, keep: Callable[[Violation], bool]) -> CheckResult:
        """A copy holding only the findings *keep* accepts, status recomputed."""
        return CheckResult.from_findings(
            check=self.check,
            violations=[v for v in self.violations if keep(v)],
            warnings=[w for w in self.warnings if keep(w)],
        )

    def to_dict(self) -> dict[str, str | list[dict[str, str | int | bool | None]]]:
        return {
            "check": self.check,
            "status": self.status.value,
            "violations": [v.to_dict() for v in self.violations],
            "warnings": [v.to_dict() for v in self.warnings],
        }

    def format_human(self) -> str:
        lines = [f"[{self.status.value}] {self.check}"]
        for v in self.violations:
            lines.append(v.format_human())
        for w in self.warnings:
            lines.append(w.format_human(label="WARNING"))
        lines.append(
            f"--- {self.check}: {len(self.violations)} violations, {len(self.warnings)} warnings ---",
        )
        return "\n".join(lines)


class Check(Protocol):
    """Protocol that all checks must implement.

    A check may declare ``scope = "tree"`` (default ``"file"``) to mark that it
    compares or aggregates across files. Under cascading per-directory config a
    file-scoped check runs once per region; a tree-scoped check runs once at the
    scan root, because partitioning it would hide findings split across regions.
    """

    name: str
    description: str
    rules: list[str]

    def run(self, *, src_root: str) -> CheckResult:
        """Run the check against the given source root and return results."""
        ...


@runtime_checkable
class ConfigurableCheck(Protocol):
    """A check that accepts a ``[tool.lanorme.<name>]`` settings table."""

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply configuration to the check before it runs."""
        ...


@runtime_checkable
class ResultAuditor(Protocol):
    """A check that judges the other checks' results rather than the tree.

    The runner hands it every other check's result, keyed by registry name,
    once they are all in; ``run()`` stays the standalone path (``--check``),
    where the check gathers those results itself.
    """

    def audit(self, *, results: dict[str, CheckResult]) -> CheckResult:
        """Inspect *results* and return this check's own result."""
        ...


# --- Check registry ---

_registry: dict[str, Check] = {}


def register(check: Check) -> None:
    """Register a check so the unified runner can discover it."""
    _registry[check.name] = check


def get_check(name: str) -> Check | None:
    """Get a registered check by name."""
    return _registry.get(name)


def get_all_checks() -> dict[str, Check]:
    """Return all registered checks."""
    return dict(_registry)


def _crash_notice(*, check: Check, exc: BaseException) -> CheckResult:
    """The advisory result standing in for a check that raised."""
    return CheckResult.from_findings(
        check=check.name,
        warnings=[
            Violation(
                file="",
                line=0,
                rule="RUN-000: check raised an exception",
                message=(
                    f"Check {check.name!r} failed on this tree: "
                    f"{type(exc).__name__}: {str(exc)[:200] or 'no detail'}"
                ),
                fix=(
                    "This is a bug in the check (or a setting it did not validate); "
                    "the rest of the run continued"
                ),
            ),
        ],
    )


def expand_rules(*, check: Check, result: CheckResult) -> CheckResult:
    """Replace each bare rule code in *result* with the string *check* declares.

    A check may emit ``rule="SHELL-001"`` and leave the description to its
    ``rules`` list, so the description is spelled once. Every consumer (the
    human report, ndjson, the baseline's anchor for a file-level finding) then
    sees ``SHELL-001: ...``. A rule string that already carries a description,
    or a code the check does not declare, is left as it is.
    """
    declared = {extract_code(rule): rule for rule in getattr(check, "rules", []) if ":" in rule}

    def expand(finding: Violation) -> Violation:
        if ":" in finding.rule or finding.rule not in declared:
            return finding
        return replace(finding, rule=declared[finding.rule])

    return CheckResult(
        check=result.check,
        status=result.status,
        violations=[expand(v) for v in result.violations],
        warnings=[expand(w) for w in result.warnings],
    )


def run_check(check: Check, *, src_root: str) -> CheckResult:
    """Run one check, isolating any exception so it cannot abort the whole run.

    A bug in one check (a `RecursionError` on a pathological file, say) must not
    discard the results of every other check. The failure is reported as a
    warning on that check and the run continues.
    """
    try:
        return expand_rules(check=check, result=check.run(src_root=src_root))
    except Exception as exc:  # noqa: BLE001 - one check must not sink the run
        return _crash_notice(check=check, exc=exc)


def run_audit(check: ResultAuditor, *, results: dict[str, CheckResult]) -> CheckResult:
    """Run one result auditor with the same isolation as :func:`run_check`."""
    try:
        return expand_rules(check=check, result=check.audit(results=results))
    except Exception as exc:  # noqa: BLE001 - one check must not sink the run
        return _crash_notice(check=check, exc=exc)


def run_all(*, src_root: str) -> list[CheckResult]:
    """Run all registered checks and return their results, in registry order.

    Result auditors run last, over the results the other checks produced, so
    the tree is walked once per check rather than once more for the audit.
    """
    by_name: dict[str, CheckResult] = {}
    for name, check in _registry.items():
        if not isinstance(check, ResultAuditor):
            by_name[name] = run_check(check, src_root=src_root)
    for name, check in _registry.items():
        if isinstance(check, ResultAuditor):
            by_name[name] = run_audit(check, results=by_name)
    return [by_name[name] for name in _registry]
