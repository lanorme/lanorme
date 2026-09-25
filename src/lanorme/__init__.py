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
import warnings as _warnings
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from lanorme.scan import Scan, activate

__version__ = "0.20.0"


class Status(enum.Enum):
    """Result status for a check run."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Violation:
    """A single rule violation found by a check."""

    file: str
    line: int
    rule: str
    message: str
    fix: str

    @property
    def code(self) -> str:
        """The rule code (e.g. ``DRY-001``) parsed from the rule string."""
        return self.rule.split(":", 1)[0].strip().split()[0]

    def to_dict(self) -> dict[str, str | int]:
        return {
            "file": self.file,
            "line": self.line,
            "code": self.code,
            "rule": self.rule,
            "message": self.message,
            "fix": self.fix,
        }

    def format_human(self) -> str:
        return (
            f"  VIOLATION: {self.file}:{self.line} — {self.message}\n"
            f"    Rule: {self.rule}\n"
            f"    Fix: {self.fix}"
        )


@dataclass(init=False)
class CheckResult:
    """Result of running a single check.

    ``status`` is derived from the findings: any violation is ``FAIL``, else any
    warning is ``WARN``, else ``PASS``. Build one with :meth:`from_findings` or
    the constructor; the constructor's ``status=`` argument is deprecated and
    ignored, since a stored status could disagree with the findings it sits
    beside.
    """

    check: str
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)

    def __init__(
        self,
        check: str,
        status: Status | None = None,
        violations: list[Violation] | None = None,
        warnings: list[Violation] | None = None,
    ) -> None:
        self.check = check
        self.violations = [] if violations is None else violations
        self.warnings = [] if warnings is None else warnings
        if status is not None:
            _warn_stored_status(given=status, derived=self.status)

    @classmethod
    def from_findings(
        cls,
        *,
        check: str,
        violations: Iterable[Violation] = (),
        warnings: Iterable[Violation] = (),
    ) -> CheckResult:
        """Build a result from its findings; the status follows from them."""
        return cls(check=check, violations=list(violations), warnings=list(warnings))

    @property
    def status(self) -> Status:
        """``FAIL`` on any violation, else ``WARN`` on any warning, else ``PASS``."""
        if self.violations:
            return Status.FAIL
        if self.warnings:
            return Status.WARN
        return Status.PASS

    def to_dict(self) -> dict[str, str | list[dict[str, str | int]]]:
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
            lines.append(w.format_human())
        lines.append(
            f"--- {self.check}: {len(self.violations)} violations, {len(self.warnings)} warnings ---"
        )
        return "\n".join(lines)


def _warn_stored_status(*, given: Status, derived: Status) -> None:
    """Deprecation notice for ``CheckResult(status=...)``, louder when it disagrees."""
    if given is derived:
        detail = "it is derived from the findings and the argument is ignored"
    else:
        detail = f"it is derived from the findings ({derived.value}); {given.value} was ignored"
    _warnings.warn(
        f"CheckResult(status=...) is deprecated: {detail}. Build the result with "
        "CheckResult.from_findings() or drop the status argument.",
        DeprecationWarning,
        stacklevel=3,
    )


class Check(Protocol):
    """Protocol that all checks must implement.

    A check receives the :class:`~lanorme.scan.Scan` for the pass, which knows
    the root to walk, the excludes in force, and caches sources and parsed
    modules across the checks of a run. The previous entry point,
    ``run(self, *, src_root: str)``, is deprecated: the runner still calls it
    on a check that defines no ``check`` method, with a ``DeprecationWarning``
    once per check class, until it is removed.

    A check may declare ``scope = "tree"`` (default ``"file"``) to mark that it
    compares or aggregates across files. Under cascading per-directory config a
    file-scoped check runs once per region; a tree-scoped check runs once at the
    scan root, because partitioning it would hide findings split across regions.
    """

    name: str
    description: str
    rules: list[str]

    def check(self, scan: Scan) -> CheckResult:
        """Run the check over *scan* and return its findings."""
        ...


@runtime_checkable
class Configurable(Protocol):
    """A check that accepts a ``[tool.lanorme.<name>]`` settings table."""

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply configuration to the check before it runs."""
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


# Check classes already told that ``run(*, src_root)`` is deprecated, so a run
# over many regions warns once per class rather than once per call.
_legacy_run_warned: set[type] = set()


def _warn_legacy_run_once(check: Check, *, message: str) -> None:
    """Emit a ``run(*, src_root)`` deprecation once per check class."""
    check_type = type(check)
    if check_type in _legacy_run_warned:
        return
    _legacy_run_warned.add(check_type)
    _warnings.warn(message, DeprecationWarning, stacklevel=4)


def invoke_check(check: Check, *, scan: Scan) -> CheckResult:
    """Call the check's entry point over *scan*, letting any exception escape.

    ``check(scan)`` when the check defines it, else the deprecated
    ``run(src_root=...)``. Either way the scan is active for the call, so a
    helper that still takes a bare root prunes what the scan prunes.
    """
    entry = getattr(check, "check", None)
    with activate(scan):
        if callable(entry):
            return entry(scan)
        _warn_legacy_run_once(
            check,
            message=(
                f"Check {check.name!r} defines run(*, src_root) but no check(scan): "
                "implement check(self, scan) -> CheckResult; the run() entry point "
                "is deprecated and will be removed."
            ),
        )
        return check.run(src_root=str(scan.root))


def run_via_check(check: Check, *, src_root: str) -> CheckResult:
    """Serve a deprecated ``run(*, src_root)`` call through ``check(scan)``.

    The built-in checks keep ``run`` as a thin wrapper that delegates here: it
    warns once per class that ``run`` is deprecated, then runs the check over a
    scan of *src_root* under the excludes in effect.
    """
    _warn_legacy_run_once(
        check,
        message=(
            f"{type(check).__name__}.run(src_root=...) is deprecated and will be "
            "removed: call check(scan) with a lanorme.scan.Scan instead."
        ),
    )
    return invoke_check(check, scan=Scan.for_root(src_root))


def _resolve_scan(*, scan: Scan | None, src_root: str | None) -> Scan:
    """The scan to run over: the one given, else one built from *src_root*."""
    if scan is not None:
        return scan
    if src_root is None:
        raise TypeError("run_check() needs a scan or a src_root")
    return Scan.for_root(src_root)


def run_check(
    check: Check, *, scan: Scan | None = None, src_root: str | None = None
) -> CheckResult:
    """Run one check, isolating any exception so it cannot abort the whole run.

    A bug in one check (a `RecursionError` on a pathological file, say) must not
    discard the results of every other check. The failure is reported as a
    warning on that check and the run continues. Pass the *scan* to run over;
    *src_root* alone builds a scan of that path under the excludes in effect.
    """
    resolved = _resolve_scan(scan=scan, src_root=src_root)
    try:
        return invoke_check(check, scan=resolved)
    except Exception as exc:  # noqa: BLE001 - one check must not sink the run
        return CheckResult(
            check=check.name,
            warnings=[
                Violation(
                    file="",
                    line=0,
                    rule="RUN-000: check raised an exception",
                    message=f"Check {check.name!r} failed on this tree: {type(exc).__name__}",
                    fix="This is a bug in the check; the rest of the run continued",
                ),
            ],
        )


def run_all(*, scan: Scan | None = None, src_root: str | None = None) -> list[CheckResult]:
    """Run all registered checks over one scan and return their results."""
    resolved = _resolve_scan(scan=scan, src_root=src_root)
    return [run_check(check, scan=resolved) for check in _registry.values()]
