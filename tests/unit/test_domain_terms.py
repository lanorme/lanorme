"""Regression tests for TERM-NNN config loading parity (issue #17).

Both config sources must produce identical behaviour:
- pyproject.toml  -> [[tool.lanorme.domain_terms.rules]]   key: "rules"
- lanorme.toml    -> [[domain_terms.term_rules]]            key: "term_rules"

The root cause was that configure() only accepted "rules" as the vocabulary
key, while the --show-config output displays the field as ``term_rules``.
Users writing a lanorme.toml from the --show-config hints would use
"term_rules" and silently get no TERM violations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme.checks.domain_terms import DomainTermsCheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _term_codes(violations) -> set[str]:
    return {v.rule.split(":", 1)[0] for v in violations}


def _write_offending_file(tmp_py_file) -> None:
    """Write a Python file that uses the forbidden term 'Acct' as a standalone identifier."""
    tmp_py_file(name="billing/invoice.py", body="class Acct:\n    pass\n")


# ---------------------------------------------------------------------------
# Parity tests
# ---------------------------------------------------------------------------


def test_configure_accepts_pyproject_table_shape(tmp_path, tmp_py_file):
    """pyproject.toml shape: vocabulary list under the 'rules' key fires TERM-001."""
    # Arrange
    _write_offending_file(tmp_py_file)
    check = DomainTermsCheck()
    pyproject_settings = {
        "rules": [
            {"id": "TERM-001", "canonical": "Account", "forbidden": ["Acct", "Acnt"]},
        ]
    }

    # Act
    check.configure(settings=pyproject_settings)
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "TERM-001" in _term_codes(result.violations), (
        "TERM-001 must fire for forbidden term 'Acct' when configured via "
        "the pyproject.toml 'rules' key"
    )


def test_configure_accepts_lanorme_toml_shape(tmp_path, tmp_py_file):
    """lanorme.toml shape: vocabulary list under the 'term_rules' key fires TERM-001.

    Users who write a lanorme.toml derive the key name from --show-config
    output, which shows the dataclass field as ``term_rules``.  Before the
    fix, configure() silently ignored this key and produced no violations.
    """
    # Arrange
    _write_offending_file(tmp_py_file)
    check = DomainTermsCheck()
    lanorme_toml_settings = {
        "term_rules": [
            {"id": "TERM-001", "canonical": "Account", "forbidden": ["Acct", "Acnt"]},
        ]
    }

    # Act
    check.configure(settings=lanorme_toml_settings)
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "TERM-001" in _term_codes(result.violations), (
        "TERM-001 must fire for forbidden term 'Acct' when configured via "
        "the lanorme.toml 'term_rules' key"
    )


def test_rules_key_takes_precedence_over_term_rules_key(tmp_path, tmp_py_file):
    """When both 'rules' and 'term_rules' are present, 'rules' wins."""
    # Arrange
    _write_offending_file(tmp_py_file)
    check = DomainTermsCheck()
    settings = {
        "rules": [
            {"id": "TERM-001", "canonical": "Account", "forbidden": ["Acct"]},
        ],
        "term_rules": [
            {"id": "TERM-099", "canonical": "Ignored", "forbidden": ["ShouldNotFire"]},
        ],
    }

    # Act
    check.configure(settings=settings)
    result = check.run(src_root=str(tmp_path))

    # Assert
    codes = _term_codes(result.violations)
    assert "TERM-001" in codes, "'rules' key must win over 'term_rules' key"
    assert "TERM-099" not in codes, "'term_rules' key must be ignored when 'rules' is present"


def test_no_rules_configured_check_is_inert(tmp_path, tmp_py_file):
    """With no vocabulary configured, the check always passes (opt-in contract)."""
    # Arrange
    _write_offending_file(tmp_py_file)
    check = DomainTermsCheck()

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert result.violations == [], "check must be inert when no vocabulary is configured"
