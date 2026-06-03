"""Tests for SECRET-002 (.env) and SECRET-003 (.yml/.yaml) secret scanning.

Each rule has a positive (must fire) and a negative (must not fire) case to
lock in the heuristic contract. File content is written directly to tmp_path;
the tmp_py_file fixture is not used here because the inputs are not Python.
"""

from __future__ import annotations

from lanorme.checks.secrets import SecretsCheck


def _codes(violations) -> set[str]:
    return {v.rule for v in violations}


def test_secret002_fires_on_env_file_with_api_key(tmp_path):
    # Arrange
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=aB3dEfGhIjKlMnOpQrStUvWxYz012345\n", encoding="utf-8")

    # Act
    result = SecretsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SECRET-002: No hardcoded secrets in .env files" in _codes(result.violations)


def test_secret002_does_not_fire_on_placeholder_value(tmp_path):
    # Arrange
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=changeme\n", encoding="utf-8")

    # Act
    result = SecretsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SECRET-002: No hardcoded secrets in .env files" not in _codes(result.violations)


def test_secret003_fires_on_yaml_with_secret_key(tmp_path):
    # Arrange
    yaml_file = tmp_path / "config.yml"
    yaml_file.write_text("database_password: xK9mLpQrStUvWxYz0123456789abcde\n", encoding="utf-8")

    # Act
    result = SecretsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SECRET-003: No hardcoded secrets in .yml/.yaml files" in _codes(result.violations)


def test_secret003_does_not_fire_on_safe_yaml_value(tmp_path):
    # Arrange
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("database_password: <your-password-here>\n", encoding="utf-8")

    # Act
    result = SecretsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SECRET-003: No hardcoded secrets in .yml/.yaml files" not in _codes(result.violations)
