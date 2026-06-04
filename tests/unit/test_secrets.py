"""Tests for SECRETPY-001 hardcoded-secret detection.

The check is precision-first: it flags credential-named variables bound to
real-looking string literals and self-betraying secret shapes (PEM, JWT,
vendor-prefixed tokens, credential URLs), while deliberately exempting
environment lookups, placeholders, structural names, and test scaffolding.
Each test asserts on the specific finding (file, line, message), not merely
that something fired.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.secrets import SecretsCheck


@pytest.fixture
def write(tmp_path: Path):
    """Write a single source file and return the run result for the tree."""

    def _write(name: str, source: str):
        (tmp_path / name).write_text(source, encoding="utf-8")
        return SecretsCheck().run(src_root=str(tmp_path))

    return _write


# --- Positive cases: a real credential is flagged ------------------------


def test_named_credential_assignment_is_flagged(write):
    # Arrange: a secret-named variable bound to a real-looking literal.
    result = write("config.py", 'password = "s3cr3t-prod-value"\n')

    # Act / Assert: exactly one violation naming the bound variable.
    assert result.status == Status.FAIL
    assert len(result.violations) == 1
    violation = result.violations[0]
    assert violation.code == "SECRETPY-001"
    assert violation.file == "config.py"
    assert violation.line == 1
    assert violation.message == "Hardcoded credential value bound to 'password'"


def test_call_kwarg_credential_is_flagged(write):
    # Arrange: a credential passed as a keyword argument.
    result = write("db.py", 'connect(host="db", password="hunter2hunter2")\n')

    # Act / Assert: the kwarg name appears in the message.
    assert result.status == Status.FAIL
    assert len(result.violations) == 1
    assert result.violations[0].message == "Hardcoded credential value bound to 'password'"


def test_vendor_shaped_token_is_flagged_regardless_of_name(write):
    # Arrange: an AWS access-key shape bound to an innocuous name.
    result = write("aws.py", 'note = "AKIAIOSFODNN7EXAMPLE"\n')

    # Act / Assert: shape detection fires with the vendor description.
    assert result.status == Status.FAIL
    assert len(result.violations) == 1
    assert "AWS access-key ID literal" in result.violations[0].message


# --- Negative cases: documented exemptions are not flagged ---------------


def test_environment_lookup_is_not_flagged(write):
    # Arrange: the credential is read from the environment, not hardcoded.
    result = write("settings.py", "import os\npassword = os.environ['DB_PASSWORD']\n")

    # Act / Assert: no literal, so nothing fires.
    assert result.status == Status.PASS
    assert not result.violations


def test_placeholder_value_is_not_flagged(write):
    # Arrange: a credential name bound to an obvious placeholder.
    result = write("example.py", 'api_key = "your-api-key-here"\n')

    # Act / Assert: placeholder markers suppress the finding.
    assert result.status == Status.PASS
    assert not result.violations


def test_test_file_is_skipped_wholesale(write):
    # Arrange: a real-looking secret living in a ``test_`` file.
    result = write("test_login.py", 'password = "realLookingValue42"\n')

    # Act / Assert: test files are exempt, so the result is clean.
    assert result.status == Status.PASS
    assert not result.violations


def test_structural_last_segment_name_is_not_flagged(write):
    # Arrange: a "secret_pattern" name whose last segment is structural.
    result = write("forms.py", 'secret_pattern = "tokenizedValue99"\n')

    # Act / Assert: structural last segments are not credentials.
    assert result.status == Status.PASS
    assert not result.violations


# --- Boundary cases: the line between flagged and exempt ------------------


def test_literal_below_minimum_length_is_not_flagged(write):
    # Arrange: a credential name bound to a 7-char literal (below the 8 floor).
    result = write("short.py", 'password = "1234567"\n')

    # Act / Assert: too short to be a real secret.
    assert result.status == Status.PASS
    assert not result.violations


def test_literal_at_minimum_length_is_flagged(write):
    # Arrange: an 8-char literal sits exactly on the inclusive boundary.
    result = write("edge.py", 'password = "12345678"\n')

    # Act / Assert: the boundary length is treated as a real secret.
    assert result.status == Status.FAIL
    assert len(result.violations) == 1
    assert result.violations[0].line == 1


def test_high_entropy_value_overrides_placeholder_marker(write):
    # Arrange: a 32+ char mixed-case-and-digit value carrying an "example"
    # marker; entropy should defeat the placeholder exemption.
    high_entropy = "Ab3" + "Xy7Qz9Kw2Mn4" * 3 + "example"
    result = write("entropy.py", f'secret_key = "{high_entropy}"\n')

    # Act / Assert: the marker is present but entropy wins, so it is flagged.
    assert result.status == Status.FAIL
    assert len(result.violations) == 1
    assert result.violations[0].message == "Hardcoded credential value bound to 'secret_key'"
