"""Unit tests for the core registry: register / get_check / get_all_checks.

Tests follow AAA structure with inline ``# Arrange / # Act / # Assert``
markers so the AAA-001 check passes when LaNorme dogfoods itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lanorme import (
    CheckResult,
    Status,
    Violation,
    get_all_checks,
    get_check,
    register,
)


@dataclass
class _StubCheck:
    name: str = "stub"
    description: str = "a stub for registry tests"
    rules: list[str] = field(default_factory=lambda: ["STUB-001: nothing"])

    def run(self, *, src_root: str) -> CheckResult:
        return CheckResult(check=self.name, status=Status.PASS, violations=[])


def test_register_then_get_check_returns_same_instance():
    # Arrange
    check = _StubCheck(name="registry_roundtrip_stub")

    # Act
    register(check)
    retrieved = get_check("registry_roundtrip_stub")

    # Assert
    assert retrieved is check


def test_get_all_checks_includes_a_freshly_registered_check():
    # Arrange
    check = _StubCheck(name="registry_listing_stub")
    register(check)

    # Act
    all_checks = get_all_checks()

    # Assert
    assert "registry_listing_stub" in all_checks
    assert all_checks["registry_listing_stub"] is check


def test_get_check_returns_none_for_unknown_name():
    # Arrange / Act
    result = get_check("definitely-not-a-registered-check")

    # Assert
    assert result is None


def test_violation_to_dict_round_trips_through_check_result():
    # Arrange
    violation = Violation(
        file="a.py", line=42, rule="STUB-001", message="m", fix="f"
    )
    result = CheckResult(check="stub", status=Status.FAIL, violations=[violation])

    # Act
    payload = result.to_dict()

    # Assert
    assert payload["check"] == "stub"
    assert payload["status"] == "FAIL"
    violations = payload["violations"]
    assert isinstance(violations, list)
    assert violations[0]["rule"] == "STUB-001"
    assert violations[0]["line"] == 42
