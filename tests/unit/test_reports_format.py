"""The human rendering lives in ``lanorme.reports``; the old methods only delegate."""

from __future__ import annotations

import pytest

from lanorme import CheckResult, Violation
from lanorme.reports import format_result, format_violation


@pytest.fixture
def result() -> CheckResult:
    error = Violation(file="a.py", line=3, rule="X-001: rule", message="bad", fix="mend")
    warning = Violation(file="b.py", line=0, rule="X-002: soft", message="meh", fix="maybe")
    return CheckResult.from_findings(check="x", violations=[error], warnings=[warning])


def test_format_result_renders_header_findings_and_tally(result: CheckResult) -> None:
    # Arrange / Act
    text = format_result(result)

    # Assert
    assert text == (
        "[FAIL] x\n"
        "  VIOLATION: a.py:3 — bad\n"
        "    Rule: X-001: rule\n"
        "    Fix: mend\n"
        "  WARNING: b.py:0 — meh\n"
        "    Rule: X-002: soft\n"
        "    Fix: maybe\n"
        "--- x: 1 violations, 1 warnings ---"
    )


def test_deprecated_methods_warn_and_delegate(result: CheckResult) -> None:
    # Arrange
    finding = result.violations[0]

    # Act
    with pytest.warns(DeprecationWarning, match="format_violation"):
        old_finding = finding.format_human(label="WARNING")
    with pytest.warns(DeprecationWarning, match="format_result"):
        old_result = result.format_human()

    # Assert
    assert old_finding == format_violation(finding, label="WARNING")
    assert old_result == format_result(result)
