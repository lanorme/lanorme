"""Tests for `[tool.lanorme.file_limits]` threshold configuration.

The check is default-on and stays that way: these keys retune the limits, they
do not switch the rules off, so there is no `enabled` key to exercise.

Each fixture holds one metric at a fixed value and moves the threshold around
it, so a test that passes because the default happened to agree with the
configured value is not possible. Findings are read from which list they land
in (`violations` versus `warnings`) rather than from the rule string, because
the strings no longer carry the number.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme.checks.file_limits import (
    CLASS_METHOD_WARN,
    COMPLEXITY_ERROR,
    COMPLEXITY_WARN,
    FILE_ERROR_LINES,
    FILE_WARN_LINES,
    FUNC_ERROR_LINES,
    FUNC_WARN_LINES,
    PARAM_ERROR,
    PARAM_WARN,
    FileLimitsCheck,
)

_DEFAULTS = {
    "file_warn_lines": FILE_WARN_LINES,
    "file_error_lines": FILE_ERROR_LINES,
    "func_warn_lines": FUNC_WARN_LINES,
    "func_error_lines": FUNC_ERROR_LINES,
    "class_method_warn": CLASS_METHOD_WARN,
    "complexity_warn": COMPLEXITY_WARN,
    "complexity_error": COMPLEXITY_ERROR,
    "param_warn": PARAM_WARN,
    "param_error": PARAM_ERROR,
}


@pytest.fixture
def run_with(tmp_path: Path):
    """Write *source* to a scanned file and run the check under *settings*."""

    def _run(source: str, **settings: int):
        (tmp_path / "sample.py").write_text(source, encoding="utf-8")
        check = FileLimitsCheck()
        if settings:
            check.configure(settings=dict(settings))
        return check.run(src_root=str(tmp_path))

    return _run


def _codes(findings) -> set[str]:
    """The rule codes present in a finding list."""
    return {f.rule.split(":", 1)[0] for f in findings}


def _module_of(lines: int) -> str:
    """A module body of *lines* effective lines and nothing else notable."""
    return "\n".join(f"x{i} = {i}" for i in range(lines)) + "\n"


def _function_of(lines: int) -> str:
    """A single function whose body is *lines* effective lines (plus its def)."""
    body = "\n".join(f"    y{i} = {i}" for i in range(lines - 1))
    return f"def wide():\n{body}\n"


def _class_of(methods: int) -> str:
    """A class with *methods* methods."""
    body = "\n".join(f"    def m{i}(self):\n        pass" for i in range(methods))
    return f"class Wide:\n{body}\n"


def _complexity_of(value: int) -> str:
    """A function whose cyclomatic complexity is exactly *value*."""
    branches = "\n".join(f"    if a == {i}:\n        pass" for i in range(value - 1))
    return f"def branchy(a):\n{branches}\n    return a\n"


def _params_of(count: int) -> str:
    """A module-level function taking *count* parameters."""
    params = ", ".join(f"p{i}" for i in range(count))
    return f"def wide({params}):\n    return 0\n"


# --------------------------------------------------------------------------- #
# Wiring: every documented key reaches the check
# --------------------------------------------------------------------------- #


def test_unconfigured_check_holds_the_house_defaults() -> None:
    # The control for every test below: no config means the shipped standard.
    check = FileLimitsCheck()
    assert {key: getattr(check, key) for key in _DEFAULTS} == _DEFAULTS


@pytest.mark.parametrize("key", sorted(_DEFAULTS))
def test_every_threshold_key_is_applied(key: str) -> None:
    check = FileLimitsCheck()
    check.configure(settings={key: 42})
    assert getattr(check, key) == 42


@pytest.mark.parametrize("key", sorted(_DEFAULTS))
def test_setting_one_key_leaves_the_others_at_their_defaults(key: str) -> None:
    # Arrange
    check = FileLimitsCheck()
    # Act
    check.configure(settings={key: 42})
    # Assert
    untouched = {k: v for k, v in _DEFAULTS.items() if k != key}
    assert {k: getattr(check, k) for k in untouched} == untouched


def test_configure_coerces_to_int() -> None:
    # TOML gives ints, but configure() mirrors the comments check and coerces.
    check = FileLimitsCheck()
    check.configure(settings={"param_error": "6"})
    assert check.param_error == 6


def test_unknown_keys_are_ignored() -> None:
    # Arrange
    check = FileLimitsCheck()
    # Act
    check.configure(settings={"file_error_lines": 400, "not_a_threshold": 1})
    # Assert
    assert check.file_error_lines == 400
    assert not hasattr(check, "not_a_threshold")


# --------------------------------------------------------------------------- #
# Behaviour: each rule measures against the configured number
# --------------------------------------------------------------------------- #


def test_raising_the_file_limit_clears_a_default_violation(run_with) -> None:
    # Arrange: a file over the default error threshold.
    source = _module_of(520)
    assert "SIZE-001" in _codes(run_with(source).violations)

    # Act
    relaxed = run_with(source, file_warn_lines=550, file_error_lines=600)

    # Assert
    assert "SIZE-001" not in _codes(relaxed.violations)
    assert "SIZE-001" not in _codes(relaxed.warnings)


def test_lowering_the_file_limit_fails_a_file_that_passed(run_with) -> None:
    # Arrange: a file comfortably inside the default limit.
    source = _module_of(120)
    assert "SIZE-001" not in _codes(run_with(source).violations)

    # Act
    tightened = run_with(source, file_warn_lines=50, file_error_lines=100)

    # Assert
    assert "SIZE-001" in _codes(tightened.violations)


def test_configured_file_warn_band_reports_a_warning_not_a_violation(run_with) -> None:
    result = run_with(_module_of(120), file_warn_lines=100, file_error_lines=200)
    assert "SIZE-001" in _codes(result.warnings)
    assert "SIZE-001" not in _codes(result.violations)


@pytest.mark.parametrize(
    ("effective", "expected_violation"),
    [(99, False), (100, True)],
)
def test_configured_file_limit_fires_at_the_boundary(
    run_with, effective: int, expected_violation: bool
) -> None:
    # The comparison stays >=, so the limit itself is a violation.
    result = run_with(_module_of(effective), file_warn_lines=10, file_error_lines=100)
    assert ("SIZE-001" in _codes(result.violations)) is expected_violation


def test_function_length_limit_is_configurable(run_with) -> None:
    # Arrange
    source = _function_of(30)
    assert "SIZE-002" not in _codes(run_with(source).violations)

    # Act
    tightened = run_with(source, func_warn_lines=10, func_error_lines=20)

    # Assert
    assert "SIZE-002" in _codes(tightened.violations)


def test_class_method_limit_is_configurable(run_with) -> None:
    # Arrange
    source = _class_of(6)
    assert "SIZE-003" not in _codes(run_with(source).warnings)

    # Act
    tightened = run_with(source, class_method_warn=5)

    # Assert
    assert "SIZE-003" in _codes(tightened.warnings)


def test_complexity_limit_is_configurable(run_with) -> None:
    # Arrange
    source = _complexity_of(8)
    assert "COMPLEXITY-001" not in _codes(run_with(source).violations)

    # Act
    tightened = run_with(source, complexity_warn=3, complexity_error=5)

    # Assert
    assert "COMPLEXITY-001" in _codes(tightened.violations)


def test_parameter_limit_is_configurable(run_with) -> None:
    # Arrange
    source = _params_of(4)
    assert "PARAM-001" not in _codes(run_with(source).violations)
    assert "PARAM-001" not in _codes(run_with(source).warnings)

    # Act
    tightened = run_with(source, param_warn=2, param_error=3)

    # Assert
    assert "PARAM-001" in _codes(tightened.violations)


# --------------------------------------------------------------------------- #
# A warn threshold above its error threshold
# --------------------------------------------------------------------------- #


def test_warn_above_error_collapses_the_warn_band(run_with) -> None:
    # 150 sits between the two, where the inverted pair describes no band. The
    # error threshold wins, so this is a violation rather than a warning.
    result = run_with(_module_of(150), file_warn_lines=600, file_error_lines=100)
    assert "SIZE-001" in _codes(result.violations)
    assert "SIZE-001" not in _codes(result.warnings)


def test_warn_above_error_still_passes_below_the_error(run_with) -> None:
    result = run_with(_module_of(50), file_warn_lines=600, file_error_lines=100)
    assert "SIZE-001" not in _codes(result.violations)
    assert "SIZE-001" not in _codes(result.warnings)


# --------------------------------------------------------------------------- #
# Baseline anchors must not move when a threshold moves
# --------------------------------------------------------------------------- #


def _rule_strings(result) -> set[str]:
    return {f.rule for f in [*result.violations, *result.warnings]}


def test_rule_strings_carry_no_threshold_number(run_with) -> None:
    # baseline.py anchors a file-level finding on a hash of its rule
    # description, so a number in the rule string would make every configured
    # project miss its own committed baseline entries. Guard the whole set.
    result = run_with(_module_of(520), file_warn_lines=100, file_error_lines=200)
    assert _rule_strings(result), "fixture produced no findings to inspect"
    for rule in _rule_strings(result):
        assert not any(char.isdigit() for char in rule.split(":", 1)[1]), rule


def test_same_finding_keeps_its_rule_string_across_thresholds(run_with) -> None:
    # The regression for #58: tightening the limit must leave the anchor of an
    # already-recorded violation untouched.
    strict = run_with(_module_of(520), file_error_lines=300)
    looser = run_with(_module_of(520), file_error_lines=500)
    assert _rule_strings(strict) == _rule_strings(looser)
