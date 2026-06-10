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

from lanorme import CheckResult, Status, Violation
from lanorme.checks import meta as meta_module
from lanorme.checks.meta import (
    MetaCheck,
    _validate_description,
    _validate_name,
    _validate_result_check_name,
    _validate_rules,
    _validate_violation_fields,
)


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

    def run(self, *, src_root: str) -> CheckResult:
        return CheckResult(
            check=self._result_check,
            status=Status.PASS,
            violations=list(self._violations),
            warnings=list(self._warnings),
        )


def _install_registry(monkeypatch, checks: dict[str, object]) -> None:
    """Point the meta run loop at *checks*, including the real MetaCheck self."""
    registry = {**checks, "meta": MetaCheck()}
    monkeypatch.setattr(meta_module, "get_all_checks", lambda: registry)


def _good_violation() -> Violation:
    """A fully-populated violation that must satisfy META-005."""
    return Violation(file="a.py", line=0, rule="X-001: r", message="m", fix="f")


# --- Helper-level unit tests (META-001..005 in isolation) ---


def test_validate_name_flags_empty_and_whitespace():
    # Arrange / Act
    empty = _validate_name(check_name="")
    blank = _validate_name(check_name="   ")
    ok = _validate_name(check_name="layer_deps")

    # Assert
    assert empty is not None and empty.code == "META-001"
    assert blank is not None and blank.code == "META-001"
    assert ok is None


def test_validate_description_flags_only_empty():
    # Arrange / Act
    flagged = _validate_description(check_name="c", description="  ")
    ok = _validate_description(check_name="c", description="real description")

    # Assert
    assert flagged is not None and flagged.code == "META-002"
    assert ok is None


def test_validate_rules_flags_empty_list():
    # Arrange / Act
    flagged = _validate_rules(check_name="c", rules=[])
    ok = _validate_rules(check_name="c", rules=["R-001"])

    # Assert
    assert flagged is not None and flagged.code == "META-003"
    assert ok is None


def test_validate_result_check_name_requires_exact_match():
    # Arrange: a result whose check field has trailing whitespace is NOT a match.
    matching = CheckResult(check="c", status=Status.PASS)
    trailing = CheckResult(check="c ", status=Status.PASS)

    # Act
    ok = _validate_result_check_name(check_name="c", result=matching)
    flagged = _validate_result_check_name(check_name="c", result=trailing)

    # Assert
    assert ok is None
    assert flagged is not None and flagged.code == "META-004"


def test_validate_violation_fields_reports_each_missing_string_field():
    # Arrange: file, rule and fix are empty/blank; message is present.
    bad = Violation(file="", line=0, rule="  ", message="m", fix="")

    # Act
    problems = _validate_violation_fields(
        check_name="c", violation=bad, source="violation"
    )

    # Assert: one META-005 finding per missing field, line never counts.
    assert {p.code for p in problems} == {"META-005"}
    missing = sorted(p.message.split("empty '")[1].split("'")[0] for p in problems)
    assert missing == ["file", "fix", "rule"]


def test_validate_violation_fields_accepts_zero_line():
    # Arrange: line is the integer 0 but every required string field is present.
    good = _good_violation()

    # Act
    problems = _validate_violation_fields(
        check_name="c", violation=good, source="violation"
    )

    # Assert: line is not a required field, so a zero line is fine.
    assert problems == []


# --- Full run() tests against an injected registry ---


def test_run_passes_when_all_checks_well_formed(monkeypatch):
    # Arrange: two perfectly well-formed checks beside the real meta self.
    _install_registry(
        monkeypatch,
        {
            "alpha": _FakeCheck(name="alpha", violations=[_good_violation()]),
            "beta": _FakeCheck(name="beta", warnings=[_good_violation()]),
        },
    )

    # Act
    result = MetaCheck().run(src_root="/tmp")

    # Assert
    assert result.check == "meta"
    assert result.status is Status.PASS
    assert result.violations == []


def test_run_flags_empty_name(monkeypatch):
    # Arrange: a check with an empty name attribute.
    _install_registry(monkeypatch, {"k": _FakeCheck(name="")})

    # Act
    result = MetaCheck().run(src_root="/tmp")

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
    result = MetaCheck().run(src_root="/tmp")

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
    result = MetaCheck().run(src_root="/tmp")

    # Assert
    assert result.status is Status.FAIL
    assert [v.code for v in result.violations] == ["META-004"]


def test_run_flags_violation_with_empty_field(monkeypatch):
    # Arrange: a check that emits a violation missing its message.
    bad = Violation(file="a.py", line=1, rule="X-001", message="", fix="f")
    _install_registry(monkeypatch, {"k": _FakeCheck(name="k", violations=[bad])})

    # Act
    result = MetaCheck().run(src_root="/tmp")

    # Assert
    assert result.status is Status.FAIL
    assert [v.code for v in result.violations] == ["META-005"]


def test_run_validates_warnings_too(monkeypatch):
    # Arrange: the malformed finding is a WARNING, not a violation.
    bad = Violation(file="a.py", line=1, rule="X-001", message="m", fix="")
    _install_registry(monkeypatch, {"k": _FakeCheck(name="k", warnings=[bad])})

    # Act
    result = MetaCheck().run(src_root="/tmp")

    # Assert: META-005 still fires and the message identifies it as a warning.
    assert result.status is Status.FAIL
    assert len(result.violations) == 1
    assert result.violations[0].code == "META-005"
    assert "warning" in result.violations[0].message


def test_run_skips_self(monkeypatch):
    # Arrange: only the real MetaCheck is registered (installed by the helper).
    _install_registry(monkeypatch, {})

    # Act: meta must not introspect itself (no infinite recursion, no findings).
    result = MetaCheck().run(src_root="/tmp")

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
    result = MetaCheck().run(src_root="/tmp")

    # Assert
    assert result.status is Status.PASS
    assert result.violations == []
