"""META-001 through META-005: Meta-check that validates all other checks produce structured output.

Inspects every registered check (excluding itself) and verifies:
    META-001  Check has a non-empty ``name`` attribute
    META-002  Check has a non-empty ``description`` attribute
    META-003  Check has a non-empty ``rules`` list
    META-004  When run, check returns a ``CheckResult`` with the correct ``check`` name
    META-005  All violations in the result have non-empty ``file``, ``rule``, ``message``, ``fix``

In a full run the runner hands this check the results the other checks already
produced (the ``ResultAuditor`` protocol), so nothing is run twice. Standalone
(``--check=meta``) it runs the other checks itself.

Run:
    lanorme check . --check=meta
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lanorme import CheckResult, Violation, get_all_checks, register, run_check
from lanorme.scan import Scan


def _validate_name(*, check_name: str) -> Violation | None:
    """META-001: Verify the check has a non-empty name."""
    if not check_name or not check_name.strip():
        return Violation(
            file="checks/",
            line=0,
            rule="META-001: Check must have a non-empty name",
            message="Registered check has an empty or missing name",
            fix="Set a non-empty `name` attribute on the check class",
        )
    return None


def _validate_description(*, check_name: str, description: str) -> Violation | None:
    """META-002: Verify the check has a non-empty description."""
    if not description or not description.strip():
        return Violation(
            file=f"checks/ ({check_name})",
            line=0,
            rule="META-002: Check must have a non-empty description",
            message=f"Check '{check_name}' has an empty or missing description",
            fix="Set a non-empty `description` attribute on the check class",
        )
    return None


def _validate_rules(*, check_name: str, rules: list[str]) -> Violation | None:
    """META-003: Verify the check has a non-empty rules list."""
    if not rules:
        return Violation(
            file=f"checks/ ({check_name})",
            line=0,
            rule="META-003: Check must have a non-empty rules list",
            message=f"Check '{check_name}' has an empty rules list",
            fix="Add at least one rule string to the `rules` list",
        )
    return None


def _validate_result_check_name(
    *,
    check_name: str,
    result: CheckResult,
) -> Violation | None:
    """META-004: Verify the result's ``check`` field matches the check's name."""
    if result.check != check_name:
        return Violation(
            file=f"checks/ ({check_name})",
            line=0,
            rule="META-004: CheckResult.check must match the check's name",
            message=(
                f"Check '{check_name}' returned a CheckResult with "
                f"check='{result.check}' (expected '{check_name}')"
            ),
            fix="Return CheckResult(check=self.name, ...) from the check() method",
        )
    return None


def _validate_violation_fields(
    *,
    check_name: str,
    violation: Violation,
    source: str,
) -> list[Violation]:
    """META-005: Verify a violation has non-empty required fields."""
    problems: list[Violation] = []
    required_fields = ("file", "rule", "message", "fix")

    for field_name in required_fields:
        value = getattr(violation, field_name, "")
        if not value or (isinstance(value, str) and not value.strip()):
            problems.append(
                Violation(
                    file=f"checks/ ({check_name})",
                    line=0,
                    rule="META-005: Violations must have non-empty file, rule, message, and fix",
                    message=(
                        f"Check '{check_name}' produced a {source} with empty '{field_name}' field"
                    ),
                    fix=f"Ensure all Violation instances have a non-empty `{field_name}`",
                ),
            )

    return problems


@dataclass
class MetaCheck:
    """Meta-check: validates all checks produce structured output."""

    name: str = "meta"
    description: str = "Meta-check: validates all checks produce structured output"
    scope = "tree"  # introspects the whole check registry, not a file partition
    rules: list[str] = field(
        default_factory=lambda: [
            "META-001: Check must have a non-empty name",
            "META-002: Check must have a non-empty description",
            "META-003: Check must have a non-empty rules list",
            "META-004: CheckResult.check must match the check's name",
            "META-005: Violations must have non-empty file, rule, message, and fix",
        ],
    )

    def check(self, scan: Scan) -> CheckResult:
        """Standalone entry: run every other check over *scan*, then audit what they returned."""
        results = {
            check_name: run_check(check, scan=scan)
            for check_name, check in get_all_checks().items()
            if check_name != self.name
        }
        return self.audit(results=results)

    def audit(self, *, results: dict[str, CheckResult]) -> CheckResult:
        """Inspect every registered check (excluding self) and its result in *results*."""
        violations: list[Violation] = []
        for check_name, check in sorted(get_all_checks().items()):
            # Skip self to avoid auditing our own (not yet produced) result.
            if check_name == self.name:
                continue
            violations.extend(_collect_static_violations(check=check))
            result = results.get(check_name)
            if result is not None:
                violations.extend(_collect_result_violations(check_name=check.name, result=result))
        return CheckResult.from_findings(check=self.name, violations=violations)


def _collect_static_violations(*, check: object) -> list[Violation]:
    """META-001..003: the declarations a check must carry."""
    found: list[Violation] = []
    name = getattr(check, "name", "")
    for violation in (
        _validate_name(check_name=name),
        _validate_description(check_name=name, description=getattr(check, "description", "")),
        _validate_rules(check_name=name, rules=getattr(check, "rules", [])),
    ):
        if violation:
            found.append(violation)
    return found


def _collect_result_violations(*, check_name: str, result: CheckResult) -> list[Violation]:
    """META-004..005: the shape of what a check returned."""
    found: list[Violation] = []
    mismatch = _validate_result_check_name(check_name=check_name, result=result)
    if mismatch:
        found.append(mismatch)
    for source, findings in (("violation", result.violations), ("warning", result.warnings)):
        for finding in findings:
            found.extend(
                _validate_violation_fields(check_name=check_name, violation=finding, source=source),
            )
    return found


# Self-register on import.
register(MetaCheck())
