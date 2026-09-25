"""``CheckResult``: the status follows the findings and cannot disagree with them.

Any violation is FAIL, else any warning is WARN, else PASS. The constructor
still accepts ``status=`` for one release, ignoring it with a
``DeprecationWarning``.
"""

from __future__ import annotations

import warnings

import pytest

from lanorme import CheckResult, Status, Violation


def build_finding(code: str = "X-001") -> Violation:
    return Violation(file="m.py", line=1, rule=f"{code}: r", message="m", fix="f")


def test_status_is_derived_from_the_findings():
    # Arrange / Act
    clean = CheckResult(check="c")
    advisory = CheckResult(check="c", warnings=[build_finding()])
    failing = CheckResult(check="c", violations=[build_finding()], warnings=[build_finding()])

    # Assert: any violation fails, else any warning warns, else it passes.
    assert clean.status is Status.PASS
    assert advisory.status is Status.WARN
    assert failing.status is Status.FAIL


def test_status_follows_findings_added_after_construction():
    # Arrange
    result = CheckResult(check="c")

    # Act: a finding appended later still counts, in the record too.
    result.violations.append(build_finding())

    # Assert
    assert result.status is Status.FAIL
    assert result.to_dict()["status"] == "FAIL"


def test_from_findings_builds_the_same_result_as_the_constructor():
    # Arrange
    warning = build_finding("W-001")

    # Act: any iterable is accepted and copied into a list.
    built = CheckResult.from_findings(check="c", warnings=(warning,))

    # Assert
    assert built == CheckResult(check="c", warnings=[warning])
    assert built.status is Status.WARN


def test_filtering_the_findings_moves_the_status_with_them():
    # Arrange
    result = CheckResult(
        check="c",
        violations=[build_finding("V-001")],
        warnings=[build_finding("W-001")],
    )

    # Act
    kept = result.filter_findings(lambda finding: finding.code != "V-001")

    # Assert: the violation went, so the result is now advisory.
    assert kept.status is Status.WARN
    assert [w.code for w in kept.warnings] == ["W-001"]
    assert result.status is Status.FAIL  # the original is untouched


def test_status_argument_that_disagrees_is_ignored_with_a_deprecation_warning():
    # Arrange / Act: a hand-built result claims PASS while carrying a violation.
    with pytest.warns(
        DeprecationWarning,
        match=r"derived from the findings \(FAIL\); PASS was ignored",
    ):
        result = CheckResult(check="c", status=Status.PASS, violations=[build_finding()])

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
        result = CheckResult(check="c", violations=[build_finding()])

    # Assert
    assert result.status is Status.FAIL


def test_positional_status_is_still_accepted_for_the_deprecation_cycle():
    # Arrange / Act: the old positional order was (check, status, violations, warnings).
    with pytest.warns(DeprecationWarning):
        result = CheckResult("c", Status.WARN, [], [build_finding()])

    # Assert
    assert result.check == "c"
    assert result.warnings == [build_finding()]
    assert result.status is Status.WARN
