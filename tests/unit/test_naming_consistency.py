"""Tests for the naming_consistency check (NAMING-001..004).

NAMING-001/002 (repository & service CRUD prefixes) are opt-in (default-off)
because the audit found they conflict with DDD ubiquitous-language verbs that
the TERM check protects; nothing in those rules may fire unless repo_crud /
service_crud is configured, and domain verbs without a forbidden prefix
(approve_loan, transfer_funds) must always stay silent. NAMING-003 (endpoint
verb match) and NAMING-004 (boolean prefix) are always-on warnings.

The dir-scoped rules (001/002/003) key off the path relative to ``src_root``,
so every fixture here is scoped via the *directory* root, not a single file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.naming_consistency import NamingConsistencyCheck


def _write(*, root: Path, rel: str, body: str) -> None:
    """Write *body* to ``root/rel``, creating parent dirs."""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def _codes(violations) -> list[str]:
    """Bare rule codes (text before the first colon) for the given findings."""
    return [v.rule.split(":")[0] for v in violations]


# --------------------------------------------------------------------------- #
# NAMING-003: endpoint handler verb matching (always-on warning)
# --------------------------------------------------------------------------- #


def test_naming003_flags_get_handler_without_get_or_list_prefix(tmp_path: Path):
    # Arrange: a GET endpoint named with a synonym prefix under the scanned dir.
    _write(
        root=tmp_path,
        rel="api/v1/endpoints/users.py",
        body=(
            "router = object()\n"
            '@router.get("/users")\n'
            "async def fetch_users():\n"
            "    return []\n"
        ),
    )
    check = NamingConsistencyCheck()

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: a NAMING-003 warning (not a violation), status WARN.
    assert result.status == Status.WARN
    assert not result.violations
    assert [w.rule.split(":")[0] for w in result.warnings] == ["NAMING-003"]
    assert "fetch_users" in result.warnings[0].message


def test_naming003_silent_on_correct_prefixes_and_exempt_and_unknown_verb(tmp_path: Path):
    # Arrange: a list_/create_/login(exempt)/patch(unknown verb) set, all clean.
    _write(
        root=tmp_path,
        rel="api/v1/endpoints/users.py",
        body=(
            "router = object()\n"
            '@router.get("/users")\n'
            "async def list_users():\n"
            "    return []\n"
            '@router.post("/users")\n'
            "async def create_user():\n"
            "    return {}\n"
            '@router.get("/login")\n'
            "async def login():\n"
            "    return {}\n"
            '@router.patch("/users/1")\n'
            "async def patch_user():\n"
            "    return {}\n"
        ),
    )
    check = NamingConsistencyCheck()

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: nothing fires - good prefixes, an exempt endpoint, an unmapped verb.
    assert result.status == Status.PASS
    assert not result.warnings
    assert not result.violations


def test_naming003_bare_decorator_attribute_does_not_fire(tmp_path: Path):
    # Arrange: ``@router.get`` used WITHOUT a call is not a Call node, so the
    # HTTP method cannot be extracted and the handler must not be flagged.
    _write(
        root=tmp_path,
        rel="api/v1/endpoints/bare.py",
        body=(
            "router = object()\n"
            "@router.get\n"
            "async def fetch_bare():\n"
            "    return []\n"
        ),
    )
    check = NamingConsistencyCheck()

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: no NAMING-003 warning for the bare-attribute decorator.
    assert result.status == Status.PASS
    assert not result.warnings


def test_naming003_ignored_for_files_outside_endpoint_dir(tmp_path: Path):
    # Arrange: the same offending handler, but NOT under api/v1/endpoints/.
    _write(
        root=tmp_path,
        rel="services/handlers.py",
        body=(
            "router = object()\n"
            '@router.get("/x")\n'
            "async def fetch_x():\n"
            "    return []\n"
        ),
    )
    check = NamingConsistencyCheck()

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: out-of-scope path -> NAMING-003 stays silent.
    assert not any(w.rule.startswith("NAMING-003") for w in result.warnings)


# --------------------------------------------------------------------------- #
# NAMING-004: boolean prefix (always-on warning, all files)
# --------------------------------------------------------------------------- #


def test_naming004_flags_bool_function_without_boolean_prefix(tmp_path: Path):
    # Arrange: a bare ``-> bool`` function whose name lacks is_/has_/can_/should_.
    _write(root=tmp_path, rel="b.py", body="def valid() -> bool:\n    return True\n")
    check = NamingConsistencyCheck()

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: a single NAMING-004 warning, status WARN, no violations.
    assert result.status == Status.WARN
    assert not result.violations
    assert [w.rule.split(":")[0] for w in result.warnings] == ["NAMING-004"]
    assert "valid" in result.warnings[0].message


def test_naming004_flags_string_bool_annotation(tmp_path: Path):
    # Arrange: a forward-ref/string ``-> "bool"`` annotation is still a bool.
    _write(root=tmp_path, rel="b.py", body='def quoted() -> "bool":\n    return True\n')
    check = NamingConsistencyCheck()

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the string annotation is treated as bool and flagged.
    assert any(w.rule.startswith("NAMING-004") for w in result.warnings)
    assert any("quoted" in w.message for w in result.warnings)


def test_naming004_silent_on_prefixed_private_and_nonbool(tmp_path: Path):
    # Arrange: a prefixed bool, a private bool, a non-bool, and an annotation-less
    # function - none should fire.
    _write(
        root=tmp_path,
        rel="b.py",
        body=(
            "def is_ok() -> bool:\n    return True\n"
            "def _hidden() -> bool:\n    return True\n"
            "def count() -> int:\n    return 1\n"
            "def untyped():\n    return True\n"
        ),
    )
    check = NamingConsistencyCheck()

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: clean run, no NAMING-004 warnings.
    assert result.status == Status.PASS
    assert not result.warnings


def test_naming004_silent_on_optional_and_union_bool(tmp_path: Path):
    # Arrange: ``Optional[bool]`` (Subscript) and ``bool | None`` (BinOp) are NOT
    # bare ``bool`` annotations, so the conservative check must stay silent.
    _write(
        root=tmp_path,
        rel="b.py",
        body=(
            "from typing import Optional\n"
            "def get_flag() -> Optional[bool]:\n    return None\n"
            "def make_widget() -> bool | None:\n    return None\n"
        ),
    )
    check = NamingConsistencyCheck()

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: no false positive on compound bool annotations.
    assert result.status == Status.PASS
    assert not result.warnings


# --------------------------------------------------------------------------- #
# NAMING-001 / 002: repository & service CRUD prefixes (opt-in, FAIL severity)
# --------------------------------------------------------------------------- #


def test_naming001_default_off_does_not_flag_forbidden_repo_prefix(tmp_path: Path):
    # Arrange: a repository method with a forbidden prefix, but no opt-in config.
    _write(
        root=tmp_path,
        rel="infrastructure/repositories/user_repo.py",
        body="class UserRepository:\n    def fetch_user(self, i):\n        ...\n",
    )
    check = NamingConsistencyCheck()  # repo_crud defaults to False

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: opt-in rule is silent by default - the cardinal precision guarantee.
    assert result.status == Status.PASS
    assert not result.violations


def test_naming001_opt_in_flags_forbidden_prefixes_but_not_domain_or_private(tmp_path: Path):
    # Arrange: a repo with forbidden prefixes plus a ubiquitous-language verb and
    # a private method that must be left alone; opt in via configure().
    _write(
        root=tmp_path,
        rel="infrastructure/repositories/user_repo.py",
        body=(
            "class UserRepository:\n"
            "    def fetch_user(self, i):\n        ...\n"
            "    def find_by_email(self, e):\n        ...\n"
            "    def remove_user(self, i):\n        ...\n"
            "    def add_user(self, u):\n        ...\n"
            "    def retrieve_all(self):\n        ...\n"
            "    def get_user(self, i):\n        ...\n"
            "    def approve_loan(self, i):\n        ...\n"
            "    def _private_fetch(self):\n        ...\n"
        ),
    )
    check = NamingConsistencyCheck()
    check.configure(settings={"repo_crud": True})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: exactly the five forbidden-prefix methods fire as NAMING-001
    # violations (FAIL); approve_loan, get_user and _private_fetch stay silent.
    assert result.status == Status.FAIL
    assert _codes(result.violations) == ["NAMING-001"] * 5
    flagged = {v.message.split("'")[1] for v in result.violations}
    assert flagged == {
        "UserRepository.fetch_user",
        "UserRepository.find_by_email",
        "UserRepository.remove_user",
        "UserRepository.add_user",
        "UserRepository.retrieve_all",
    }


def test_naming001_opt_in_still_ignores_files_outside_repo_dirs(tmp_path: Path):
    # Arrange: a forbidden-prefix method in a domain file (out of scope), opted in.
    _write(
        root=tmp_path,
        rel="domain/calc.py",
        body="class Helper:\n    def fetch_data(self):\n        ...\n",
    )
    check = NamingConsistencyCheck()
    check.configure(settings={"repo_crud": True})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: NAMING-001 is scoped to repository/persistence dirs only.
    assert result.status == Status.PASS
    assert not result.violations


def test_naming002_default_off_then_opt_in_flags_service_prefix(tmp_path: Path):
    # Arrange: a service method with a forbidden prefix.
    _write(
        root=tmp_path,
        rel="application/services/user_service.py",
        body="class UserService:\n    def fetch_profile(self):\n        ...\n",
    )

    # Act: default-off run, then an opted-in run on the same tree.
    off = NamingConsistencyCheck().run(src_root=str(tmp_path))
    on_check = NamingConsistencyCheck()
    on_check.configure(settings={"service_crud": True})
    on = on_check.run(src_root=str(tmp_path))

    # Assert: silent by default; one NAMING-002 violation once enabled.
    assert off.status == Status.PASS and not off.violations
    assert on.status == Status.FAIL
    assert _codes(on.violations) == ["NAMING-002"]