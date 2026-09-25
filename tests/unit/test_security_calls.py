"""Smoke tests for the six security_calls rules.

Each rule has a positive (must fire) and a negative (must not fire) case
to lock in the AST-shape contract from day one. Full GAN-style corpora
can be built later if the precision needs to be measured at scale.
"""

from __future__ import annotations

from lanorme.checks.security_calls import SecurityCallsCheck


def _collect_codes(violations) -> set[str]:
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
    assert "SHELL-001" in _collect_codes(result.violations)


def test_shell_001_does_not_fire_on_argv_list(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="import subprocess\nsubprocess.run(['ls', '-la'])\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SHELL-001" not in _collect_codes(result.violations)


def test_deserial_001_fires_on_pickle_loads(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="import pickle\npickle.loads(payload)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "DESERIAL-001" in _collect_codes(result.violations)


def test_deserial_001_accepts_yaml_safe_loader(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="import yaml\nyaml.load(payload, Loader=yaml.SafeLoader)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "DESERIAL-001" not in _collect_codes(result.violations)


def test_eval_001_fires_on_eval_with_variable(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="def run(expr):\n    return eval(expr)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "EVAL-001" in _collect_codes(result.violations)


def test_eval_001_does_not_fire_on_compile_with_literal(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="bytecode = compile('1+1', '<string>', 'eval')\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "EVAL-001" not in _collect_codes(result.violations)


def test_crypto_001_fires_on_hashlib_md5(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="import hashlib\nh = hashlib.md5(secret)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "CRYPTO-001" in _collect_codes(result.violations)


def test_crypto_001_accepts_md5_for_non_security_use(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="import hashlib\nh = hashlib.md5(payload, usedforsecurity=False)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "CRYPTO-001" not in _collect_codes(result.violations)


def test_tls_001_fires_on_requests_verify_false(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="import requests\nrequests.get('https://api', verify=False)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "TLS-001" in _collect_codes(result.violations)


def test_tls_001_does_not_fire_on_default_verify(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="import requests\nrequests.get('https://api')\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "TLS-001" not in _collect_codes(result.violations)


def test_debug_001_fires_on_flask_debug_true(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="from flask import Flask\napp = Flask(__name__)\napp.run(debug=True)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "DEBUG-001" in _collect_codes(result.violations)


def test_debug_001_does_not_fire_on_normal_app_run(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="from flask import Flask\napp = Flask(__name__)\napp.run(host='0.0.0.0')\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert "DEBUG-001" not in _collect_codes(result.violations)


def test_root_under_a_skip_named_ancestor_is_still_scanned(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="build/project/bad.py",
        body="import subprocess\nsubprocess.run('ls -la', shell=True)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path / "build" / "project"))

    # Assert
    assert "SHELL-001" in _collect_codes(result.violations)


# --- Import aliases and shadowing -----------------------------------------


def _collect_sites(violations) -> set[tuple[str, int]]:
    return {(v.rule, v.line) for v in violations}


def _collect_located(violations) -> list[tuple[str, str, int]]:
    return sorted((v.code, v.file, v.line) for v in violations)


def test_shell_001_resolves_a_from_import_alias(tmp_path, tmp_py_file):
    # Arrange: subprocess.run bound to another name.
    tmp_py_file(
        name="bad.py",
        body="from subprocess import run as sh\nsh(cmd, shell=True)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert _collect_sites(result.violations) == {("SHELL-001", 2)}


def test_shell_001_resolves_a_module_alias(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body="import subprocess as sp\nsp.Popen(cmd, shell=True)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert _collect_sites(result.violations) == {("SHELL-001", 2)}


def test_shell_001_ignores_a_local_def_that_shadows_the_import(tmp_path, tmp_py_file):
    # Arrange: the module rebinds ``run`` itself, so the call is not subprocess.run.
    tmp_py_file(
        name="ok.py",
        body=(
            "from subprocess import run\n\n\n"
            "def run(cmd, shell=False):\n    return (cmd, shell)\n\n\n"
            "run('ls', shell=True)\n"
        ),
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert not result.violations


def test_deserial_001_resolves_a_from_import(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(name="bad.py", body="from pickle import loads\nloads(payload)\n")

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert _collect_sites(result.violations) == {("DESERIAL-001", 2)}


def test_deserial_001_accepts_a_positional_safe_loader(tmp_path, tmp_py_file):
    # Arrange: the Loader passed positionally, the second signature PyYAML documents.
    tmp_py_file(name="ok.py", body="import yaml\nyaml.load(payload, yaml.SafeLoader)\n")

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert not result.violations


def test_eval_001_ignores_a_shadowed_eval(tmp_path, tmp_py_file):
    # Arrange: ``eval`` is a parameter, not the builtin.
    tmp_py_file(name="ok.py", body="def apply(eval, value):\n    return eval(value)\n")

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert not result.violations


def test_eval_001_fires_on_builtins_exec(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(name="bad.py", body="import builtins\nbuiltins.exec(code)\n")

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert _collect_sites(result.violations) == {("EVAL-001", 2)}


def test_crypto_001_accepts_hashlib_new_for_non_security_use(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="import hashlib\nhashlib.new('md5', payload, usedforsecurity=False)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert not result.violations


def test_crypto_001_fires_when_usedforsecurity_is_not_a_literal(tmp_path, tmp_py_file):
    # Arrange: a computed flag may be True at run time; only a literal False opts out.
    tmp_py_file(
        name="bad.py",
        body="import hashlib\nhashlib.md5(payload, usedforsecurity=strict)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert _collect_located(result.violations) == [("CRYPTO-001", "bad.py", 2)]


def test_crypto_001_ignores_a_protocol_constant_that_is_only_compared(tmp_path, tmp_py_file):
    # Arrange: line 2 rejects the protocol, line 3 uses it.
    tmp_py_file(
        name="mixed.py",
        body=(
            "import ssl\n"
            "if proto in (ssl.PROTOCOL_TLSv1, ssl.PROTOCOL_SSLv3):\n"
            "    ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1)\n"
        ),
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert _collect_sites(result.violations) == {("CRYPTO-001", 3)}


def test_tls_001_ignores_cert_none_that_is_only_compared(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body="import ssl\nif ctx.verify_mode == ssl.CERT_NONE:\n    raise RuntimeError('off')\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert not result.violations


def test_tls_001_fires_on_aiohttp_ssl_false(tmp_path, tmp_py_file):
    # Arrange: aiohttp spells the switch ``ssl=False``.
    tmp_py_file(name="bad.py", body="import aiohttp\naiohttp.TCPConnector(ssl=False)\n")

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert _collect_sites(result.violations) == {("TLS-001", 2)}


def test_debug_001_resolves_a_constructor_alias(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(name="bad.py", body="from fastapi import FastAPI as API\napp = API(debug=True)\n")

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert _collect_sites(result.violations) == {("DEBUG-001", 2)}


# --- Shadowing follows Python's scopes, not a file-wide name set ----------- #


def test_eval_001_is_not_shadowed_by_a_method_of_the_same_name(tmp_path, tmp_py_file):
    # Arrange: ``Job.exec`` binds in the class body; the module-level call is the builtin.
    tmp_py_file(
        name="bad.py",
        body="class Job:\n    def exec(self):\n        return 1\n\n\nexec(code)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert _collect_located(result.violations) == [("EVAL-001", "bad.py", 6)]


def test_shell_001_alias_is_not_shadowed_by_another_functions_parameter(tmp_path, tmp_py_file):
    # Arrange: ``helper``'s parameter ``sp`` is its own; ``launch`` still calls subprocess.
    tmp_py_file(
        name="bad.py",
        body=(
            "import subprocess as sp\n\n\n"
            "def helper(sp=None):\n    return sp\n\n\n"
            "def launch(cmd):\n    return sp.run(cmd, shell=True)\n"
        ),
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert _collect_located(result.violations) == [("SHELL-001", "bad.py", 9)]


def test_shell_001_alias_rebound_in_the_calling_function_is_unknown(tmp_path, tmp_py_file):
    # Arrange: ``launch`` binds its own ``sp``; that call is not subprocess.run.
    tmp_py_file(
        name="ok.py",
        body="import subprocess as sp\n\n\ndef launch(sp, cmd):\n    return sp.run(cmd, shell=True)\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert not result.violations


def test_eval_001_is_shadowed_inside_a_nested_function_by_the_outer_parameter(
    tmp_path,
    tmp_py_file,
):
    # Arrange: ``eval`` is ``outer``'s parameter, visible to ``inner`` as a closure.
    tmp_py_file(
        name="ok.py",
        body="def outer(eval):\n    def inner(value):\n        return eval(value)\n    return inner\n",
    )

    # Act
    result = SecurityCallsCheck().run(src_root=str(tmp_path))

    # Assert
    assert not result.violations
