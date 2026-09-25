"""Tests for the meta check (META-001..META-005).

The meta check is unusual: it does not scan files. It introspects the live
check registry and validates that every *other* registered check is
structurally well-formed -- a non-empty name/description/rules list, a
``CheckResult`` whose ``check`` field matches the check's name, and violations
(and warnings) carrying non-empty ``file``/``rule``/``message``/``fix``.

Because the inputs under test are *checks*, the fixtures here are small fake
check objects injected into the registry by monkeypatching
``meta.get_all_checks`` -- the same name the run loop reads. This mirrors how
the real check sees the registry without depending on the bundled checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import CheckResult, Status, Violation
from lanorme.checks import meta as meta_module
from lanorme.checks.meta import MetaCheck
from lanorme.scan import Scan


class _FakeCheck:
    """A minimal, well-formed check stand-in with configurable output."""

    def __init__(
        self,
        *,
        name: str = "fake",
        description: str = "a fake check",
        rules: object = None,
        result_check: object = None,
        violations: list[Violation] | None = None,
        warnings: list[Violation] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.rules = ["FAKE-001: a rule"] if rules is None else rules
        self._result_check = name if result_check is None else result_check
        self._violations = violations or []
        self._warnings = warnings or []

    def check(self, scan: Scan) -> CheckResult:
        return CheckResult(
            check=self._result_check,
            violations=list(self._violations),
            warnings=list(self._warnings),
        )


def _install_registry(monkeypatch, checks: dict[str, object]) -> None:
    """Point the meta run loop at *checks*, including the real MetaCheck self."""
    registry = {**checks, "meta": MetaCheck()}
    monkeypatch.setattr(meta_module, "get_all_checks", lambda: registry)


def _build_good_violation() -> Violation:
    """A fully-populated violation that must satisfy META-005."""
    return Violation(file="a.py", line=0, rule="X-001: r", message="m", fix="f")


# --- Each malformed shape, reported through run() at the check it names ---


@pytest.mark.parametrize(
    ("fake", "code", "fragment"),
    [
        (_FakeCheck(name="   "), "META-001", "empty or missing name"),
        (_FakeCheck(name="k", description="  "), "META-002", "description"),
        (_FakeCheck(name="k", rules=[]), "META-003", "rules list"),
        (_FakeCheck(name="k", result_check="k "), "META-004", "check='k '"),
    ],
)
def test_run_reports_each_malformed_check(monkeypatch, fake, code: str, fragment: str):
    # Arrange: one check with a single defect (a blank name, a blank
    # description, no rules, or a result whose check name does not match
    # exactly, trailing whitespace included).
    _install_registry(monkeypatch, {"k": fake})

    # Act
    result = MetaCheck().check(Scan(root=Path("/tmp")))

    # Assert: exactly that code, at the check it names, saying what is missing.
    [hit] = result.violations
    assert (hit.code, hit.file) == (code, "checks/" if code == "META-001" else "checks/ (k)")
    assert fragment in hit.message


def test_run_reports_each_missing_violation_field(monkeypatch):
    # Arrange: file, rule and fix are empty or blank; message is present.
    bad = Violation(file="", line=0, rule="  ", message="m", fix="")
    _install_registry(monkeypatch, {"k": _FakeCheck(name="k", violations=[bad])})

    # Act
    result = MetaCheck().check(Scan(root=Path("/tmp")))

    # Assert: one META-005 finding per missing field; line never counts.
    assert {v.code for v in result.violations} == {"META-005"}
    missing = sorted(v.message.split("empty '")[1].split("'")[0] for v in result.violations)
    assert missing == ["file", "fix", "rule"]


# --- Full run() tests against an injected registry ---


def test_run_passes_when_all_checks_well_formed(monkeypatch):
    # Arrange: two perfectly well-formed checks beside the real meta self.
    _install_registry(
        monkeypatch,
        {
            "alpha": _FakeCheck(name="alpha", violations=[_build_good_violation()]),
            "beta": _FakeCheck(name="beta", warnings=[_build_good_violation()]),
        },
    )

    # Act
    result = MetaCheck().check(Scan(root=Path("/tmp")))

    # Assert
    assert result.check == "meta"
    assert result.status is Status.PASS
    assert result.violations == []


def test_run_flags_empty_name(monkeypatch):
    # Arrange: a check with an empty name attribute.
    _install_registry(monkeypatch, {"k": _FakeCheck(name="")})

    # Act
    result = MetaCheck().check(Scan(root=Path("/tmp")))

    # Assert
    assert result.status is Status.FAIL
    assert [v.code for v in result.violations] == ["META-001"]


def test_run_flags_empty_description_and_rules_together(monkeypatch):
    # Arrange: a check missing both description and rules.
    _install_registry(
        monkeypatch,
        {"k": _FakeCheck(name="k", description="", rules=[])},
    )

    # Act
    result = MetaCheck().check(Scan(root=Path("/tmp")))

    # Assert: both META-002 and META-003 fire.
    assert result.status is Status.FAIL
    assert {v.code for v in result.violations} == {"META-002", "META-003"}


def test_run_flags_result_check_name_mismatch(monkeypatch):
    # Arrange: run() returns a CheckResult whose check field is wrong.
    _install_registry(
        monkeypatch,
        {"k": _FakeCheck(name="k", result_check="not-k")},
    )

    # Act
    result = MetaCheck().check(Scan(root=Path("/tmp")))

    # Assert
    assert result.status is Status.FAIL
    assert [v.code for v in result.violations] == ["META-004"]


def test_run_flags_violation_with_empty_field(monkeypatch):
    # Arrange: a check that emits a violation missing its message.
    bad = Violation(file="a.py", line=1, rule="X-001", message="", fix="f")
    _install_registry(monkeypatch, {"k": _FakeCheck(name="k", violations=[bad])})

    # Act
    result = MetaCheck().check(Scan(root=Path("/tmp")))

    # Assert
    assert result.status is Status.FAIL
    assert [v.code for v in result.violations] == ["META-005"]


def test_run_validates_warnings_too(monkeypatch):
    # Arrange: the malformed finding is a WARNING, not a violation.
    bad = Violation(file="a.py", line=1, rule="X-001", message="m", fix="")
    _install_registry(monkeypatch, {"k": _FakeCheck(name="k", warnings=[bad])})

    # Act
    result = MetaCheck().check(Scan(root=Path("/tmp")))

    # Assert: META-005 still fires and the message identifies it as a warning.
    assert result.status is Status.FAIL
    assert len(result.violations) == 1
    assert result.violations[0].code == "META-005"
    assert "warning" in result.violations[0].message


def test_run_skips_self(monkeypatch):
    # Arrange: only the real MetaCheck is registered (installed by the helper).
    _install_registry(monkeypatch, {})

    # Act: meta must not introspect itself (no infinite recursion, no findings).
    result = MetaCheck().check(Scan(root=Path("/tmp")))

    # Assert
    assert result.status is Status.PASS
    assert result.violations == []


def test_run_self_skip_keys_on_registry_name_not_attribute(monkeypatch):
    # Arrange: a non-meta check keyed under "impostor" whose .name happens to be
    # "meta". It is keyed differently from MetaCheck, so it is NOT skipped, but
    # being otherwise well-formed it produces no findings.
    _install_registry(
        monkeypatch,
        {"impostor": _FakeCheck(name="meta")},
    )

    # Act
    result = MetaCheck().check(Scan(root=Path("/tmp")))

    # Assert
    assert result.status is Status.PASS
    assert result.violations == []
