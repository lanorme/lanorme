"""Tests for ``CheckResult``: the status is derived from the findings.

A stored status could disagree with the findings beside it, so ``status`` is a
property: any violation is FAIL, else any warning is WARN, else PASS. The
constructor still accepts ``status=`` for one release, ignoring it with a
``DeprecationWarning``.
"""

from __future__ import annotations

import warnings

import pytest

from lanorme import CheckResult, Status, Violation


def _finding(code: str = "X-001") -> Violation:
    return Violation(file="m.py", line=1, rule=f"{code}: r", message="m", fix="f")


def test_status_is_derived_from_the_findings():
    # Arrange / Act
    clean = CheckResult(check="c")
    advisory = CheckResult(check="c", warnings=[_finding()])
    failing = CheckResult(check="c", violations=[_finding()], warnings=[_finding()])

    # Assert: any violation fails, else any warning warns, else it passes.
    assert clean.status is Status.PASS
    assert advisory.status is Status.WARN
    assert failing.status is Status.FAIL


def test_status_follows_findings_mutated_after_construction():
    # Arrange
    result = CheckResult(check="c")

    # Act: a finding appended later still counts.
    result.violations.append(_finding())

    # Assert
    assert result.status is Status.FAIL
    assert result.to_dict()["status"] == "FAIL"


def test_from_findings_builds_the_same_result_as_the_constructor():
    # Arrange
    warning = _finding("W-001")

    # Act: iterables are accepted and copied into lists.
    built = CheckResult.from_findings(check="c", warnings=(warning,))

    # Assert
    assert built == CheckResult(check="c", warnings=[warning])
    assert built.status is Status.WARN


def test_status_argument_that_disagrees_is_ignored_with_a_deprecation_warning():
    # Arrange / Act: a hand-built result claims PASS while carrying a violation.
    with pytest.warns(DeprecationWarning, match="derived from the findings \\(FAIL\\); PASS was ignored"):
        result = CheckResult(check="c", status=Status.PASS, violations=[_finding()])

    # Assert: the findings win.
    assert result.status is Status.FAIL


def test_status_argument_that_agrees_still_warns_it_is_deprecated():
    # Arrange / Act
    with pytest.warns(DeprecationWarning, match="the argument is ignored"):
        result = CheckResult(check="c", status=Status.PASS)

    # Assert
    assert result.status is Status.PASS


def test_omitting_status_emits_no_warning():
    # Arrange / Act
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = CheckResult(check="c", violations=[_finding()])

    # Assert
    assert result.status is Status.FAIL


def test_positional_status_is_still_accepted_for_the_deprecation_cycle():
    # Arrange / Act: the old positional order was (check, status, violations, warnings).
    with pytest.warns(DeprecationWarning):
        result = CheckResult("c", Status.WARN, [], [_finding()])

    # Assert
    assert result.check == "c"
    assert result.warnings == [_finding()]
    assert result.status is Status.WARN
