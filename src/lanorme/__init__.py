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
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable

# ``ConfigurableCheck`` is defined beside the config plumbing and re-exported here.
from lanorme.checkconfig import ConfigurableCheck as ConfigurableCheck
from lanorme.checkconfig import configure_checks
from lanorme.errors import UsageError

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
        """Deprecated: use ``lanorme.reports.format_violation``; removed in the next release."""
        # Deferred: reports imports this module, and the shim goes next release.
        from lanorme.reports import format_violation  # lanorme: ignore[IMPORT-001]

        _warn_moved(old="Violation.format_human", new="format_violation")
        return format_violation(self, label=label)


def _warn_moved(*, old: str, new: str) -> None:
    warnings.warn(
        f"{old} is deprecated and will be removed in the next release; use lanorme.reports.{new}",
        DeprecationWarning,
        stacklevel=3,
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
        """Deprecated: use ``lanorme.reports.format_result``; removed in the next release."""
        # Deferred: reports imports this module, and the shim goes next release.
        from lanorme.reports import format_result  # lanorme: ignore[IMPORT-001]

        _warn_moved(old="CheckResult.format_human", new="format_result")
        return format_result(self)


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


class Registry(Mapping[str, Check]):
    """The registered checks by name, in registration order.

    A registered check is a template: the runner never runs or configures it
    in place. :meth:`build_configured` hands each pass deep copies configured
    from that pass's config, so one region's settings cannot leak into the
    next and a second run in the same process starts from the same defaults.
    """

    def __init__(self, checks: Mapping[str, Check] | None = None) -> None:
        self._checks: dict[str, Check] = dict(checks or {})

    def register(self, check: Check) -> None:
        """Add *check*; a second, different check under a taken name is a usage error.

        Registering the same object again is a no-op, so a module imported
        twice does not fail; two plugins that pick one name do, rather than
        the later silently replacing the earlier.
        """
        existing = self._checks.get(check.name)
        if existing is not None and existing is not check:
            raise UsageError(
                f"a check named {check.name!r} is already registered "
                f"({type(existing).__module__}.{type(existing).__qualname__}); "
                f"{type(check).__module__}.{type(check).__qualname__} needs another name.",
            )
        self._checks[check.name] = check

    def unregister(self, name: str) -> None:
        """Remove the check registered under *name*, if any."""
        self._checks.pop(name, None)

    def build_configured(self, config: Mapping[str, object]) -> dict[str, Check]:
        """A deep copy of every registered check, configured from *config*.

        *config* is a resolved ``[tool.lanorme]`` table; each check's sub-table
        goes to its copy's ``configure()``, and a value it rejects is a
        :class:`~lanorme.errors.ConfigError`. The templates are never touched.
        """
        return configure_checks(templates=self._checks, config=dict(config))

    def __getitem__(self, name: str) -> Check:
        return self._checks[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._checks)

    def __len__(self) -> int:
        return len(self._checks)


_registry = Registry()


def get_registry() -> Registry:
    """The process registry the CLI loads checks into."""
    return _registry


def register(check: Check) -> None:
    """Register a check so the unified runner can discover it."""
    _registry.register(check)


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
    warning on that check and the run continues. A :class:`UsageError` is the
    exception: it is the user's mistake, not the check's, so it propagates to
    the caller (exit 2 at the CLI) rather than hiding in a crash notice.
    """
    try:
        return expand_rules(check=check, result=check.run(src_root=src_root))
    except UsageError:
        raise
    except Exception as exc:  # one check must not sink the run
        return _crash_notice(check=check, exc=exc)


def run_audit(check: ResultAuditor, *, results: dict[str, CheckResult]) -> CheckResult:
    """Run one result auditor with the same isolation as :func:`run_check`."""
    try:
        return expand_rules(check=check, result=check.audit(results=results))
    except UsageError:
        raise
    except Exception as exc:  # one check must not sink the run
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
