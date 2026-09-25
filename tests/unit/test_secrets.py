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


# --- Skip directories are matched inside the root, never above it ---------


def test_root_under_a_skip_named_ancestor_is_still_scanned(tmp_path: Path):
    # Arrange: a real credential in a project checked out under a build/ dir.
    root = tmp_path / "build" / "project"
    root.mkdir(parents=True)
    (root / "config.py").write_text('password = "s3cr3t-prod-value"\n', encoding="utf-8")

    # Act: scan the project, not its ancestor.
    result = SecretsCheck().run(src_root=str(root))

    # Assert: the ancestor is the user's filesystem, not the project layout.
    assert result.status == Status.FAIL
    assert [v.code for v in result.violations] == ["SECRETPY-001"]
    assert result.violations[0].file == "config.py"


# --- Names that point at a secret rather than hold one ---------------------


def test_environment_variable_name_binding_is_not_flagged(write):
    # Arrange: the *name* of the env var, bound under an ``_env`` / ``_var`` name.
    result = write(
        "settings.py",
        'PASSWORD_ENV = "APP_DB_PASSWORD"\ntoken_var = "GITHUB_TOKEN_VALUE"\n',
    )

    # Act / Assert: a reference to where the secret lives is not the secret.
    assert result.status == Status.PASS
    assert not result.violations


def test_secret_reference_names_are_not_flagged(write):
    # Arrange: a Secrets Manager id, a secrets file path, a hashing algorithm.
    result = write(
        "config.py",
        'secret_id = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:prod/db"\n'
        'password_file = "/run/secrets/db_password"\n'
        'PASSWORD_ALGORITHM = "pbkdf2_sha256"\n',
    )

    # Act / Assert
    assert result.status == Status.PASS
    assert not result.violations


def test_credential_phrase_wins_over_a_structural_last_segment(write):
    # Arrange: ``access_key_id`` is a credential phrase even though it ends in ``id``.
    result = write("aws.py", 'aws_access_key_id = "notanakiakeybutreal1"\n')

    # Act / Assert
    assert result.status == Status.FAIL
    assert [(v.line, v.message) for v in result.violations] == [
        (1, "Hardcoded credential value bound to 'aws_access_key_id'"),
    ]


# --- Bytes literals and generated keys -------------------------------------


def test_bytes_literal_credential_is_flagged(write):
    # Arrange: an HMAC key as a bytes literal.
    result = write("sign.py", 'hmac_secret = b"k9Q2vX7mP4sT1wZ8"\n')

    # Act / Assert
    assert result.status == Status.FAIL
    assert [(v.line, v.message) for v in result.violations] == [
        (1, "Hardcoded credential value bound to 'hmac_secret'"),
    ]


def test_django_generated_key_as_an_env_fallback_is_flagged(write):
    # Arrange: the ``django-insecure-`` dev key ships whenever the env var is unset.
    key = "django-insecure-k9q2vx7mp4st1wz8r5n3b6c0d2f4g8h1j3l5m7n9p1r3t5v7x9"
    result = write(
        "settings.py",
        f'import os\nSECRET_KEY = os.environ.get("SECRET_KEY", "{key}")\n',
    )

    # Act / Assert: the shape betrays it although the binding is a call.
    assert result.status == Status.FAIL
    assert [(v.line, v.message) for v in result.violations] == [
        (2, "Django-generated SECRET_KEY literal (django-insecure-...)"),
    ]
