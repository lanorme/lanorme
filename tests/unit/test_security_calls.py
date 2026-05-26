"""Smoke tests for the six security_calls rules.

Each rule has a positive (must fire) and a negative (must not fire) case
to lock in the AST-shape contract from day one. Full GAN-style corpora
can be built later if the precision needs to be measured at scale.
"""

from __future__ import annotations

from lanorme.checks.security_calls import SecurityCallsCheck


def _codes(violations) -> set[str]:
    return {v.rule for v in violations}


def test_shell_001_fires_on_subprocess_shell_true(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="import subprocess\nsubprocess.run('ls -la', shell=True)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SHELL-001" in _codes(result.violations)


def test_shell_001_does_not_fire_on_argv_list(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="import subprocess\nsubprocess.run(['ls', '-la'])\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SHELL-001" not in _codes(result.violations)


def test_deserial_001_fires_on_pickle_loads(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="import pickle\npickle.loads(payload)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "DESERIAL-001" in _codes(result.violations)


def test_deserial_001_accepts_yaml_safe_loader(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="import yaml\nyaml.load(payload, Loader=yaml.SafeLoader)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "DESERIAL-001" not in _codes(result.violations)


def test_eval_001_fires_on_eval_with_variable(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="def run(expr):\n    return eval(expr)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "EVAL-001" in _codes(result.violations)


def test_eval_001_does_not_fire_on_compile_with_literal(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="bytecode = compile('1+1', '<string>', 'eval')\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "EVAL-001" not in _codes(result.violations)


def test_crypto_001_fires_on_hashlib_md5(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="import hashlib\nh = hashlib.md5(secret)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "CRYPTO-001" in _codes(result.violations)


def test_crypto_001_accepts_md5_for_non_security_use(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="import hashlib\nh = hashlib.md5(payload, usedforsecurity=False)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "CRYPTO-001" not in _codes(result.violations)


def test_tls_001_fires_on_requests_verify_false(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="import requests\nrequests.get('https://api', verify=False)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "TLS-001" in _codes(result.violations)


def test_tls_001_does_not_fire_on_default_verify(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="import requests\nrequests.get('https://api')\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "TLS-001" not in _codes(result.violations)


def test_debug_001_fires_on_flask_debug_true(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="from flask import Flask\napp = Flask(__name__)\napp.run(debug=True)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "DEBUG-001" in _codes(result.violations)


def test_debug_001_does_not_fire_on_normal_app_run(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="from flask import Flask\napp = Flask(__name__)\napp.run(host='0.0.0.0')\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "DEBUG-001" not in _codes(result.violations)
