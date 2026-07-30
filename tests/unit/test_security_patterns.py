"""Tests for the security_patterns check (AUTHN-001, SQL-001, SQL-000 guard).

Two regressions of note. A long ``"a" + "a" + ...`` chain makes the mutually
recursive ``_sql_from_binop`` / ``_sql_string_from`` pair recurse on
``BinOp.left`` / ``BinOp.right`` until the stack overflows. One such file must
be skipped with a SQL-000 advisory warning, not crash the whole run, and the
rest of the tree must still be checked.

The second is AUTHN-001's layer gate: the ``api/`` layer sits below the
``source_root`` prefix on a src-layout project, so without that setting the
rule matched no file and passed without inspecting an endpoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.security_patterns import SecurityPatternsCheck

# A mutation endpoint guarded by an auth dependency, so AUTHN-001 must stay quiet.
_AUTHED_ENDPOINT = (
    "@router.post(\"/items\")\n"
    "async def make_item(\n"
    "    payload: dict,\n"
    "    current_user: Annotated[User, Depends(get_current_user)],\n"
    "):\n"
    "    return payload\n"
)

# A mutation endpoint with no auth dependency, the AUTHN-001 positive case.
_UNAUTHED_ENDPOINT = (
    "@router.post(\"/items\")\n"
    "async def make_item(payload: dict):\n"
    "    return payload\n"
)

# Raw SQL handed straight to a DB execution sink, the SQL-001 positive case.
_RAW_SQL_DAO = (
    "def fetch(db):\n"
    "    return db.execute(\"SELECT id FROM users WHERE name = 'bob'\")\n"
)

# A parameterised query with a params bag, the SQL-001 negative case.
_SAFE_SQL_DAO = (
    "def fetch(db, name):\n"
    "    return db.execute(\"SELECT id FROM users WHERE name = :name\", {\"name\": name})\n"
)


@pytest.fixture
def check() -> SecurityPatternsCheck:
    """A fresh check instance for each test."""
    return SecurityPatternsCheck()


def _write_deep_binop(path: Path, *, terms: int = 1200) -> None:
    """Write a parseable file whose ``+`` chain overflows the analyser stack."""
    chain = " + ".join(["\"a\""] * terms)
    path.write_text("x = " + chain + "\n", encoding="utf-8")


def test_deep_binop_file_is_skipped_not_crashed(
    check: SecurityPatternsCheck, tmp_path: Path
):
    # Arrange: a file with a long '+' chain that overflows the recursive SQL
    # walk, beside a genuine raw-SQL violation in another file.
    _write_deep_binop(tmp_path / "deep.py")
    (tmp_path / "dao.py").write_text(_RAW_SQL_DAO, encoding="utf-8")

    # Act: the run must complete rather than raise RecursionError.
    result = check.run(src_root=str(tmp_path))

    # Assert: the deep file is skipped with a SQL-000 warning, and the genuine
    # raw SQL elsewhere is still detected.
    assert result.status == Status.FAIL
    deep_warnings = [w for w in result.warnings if w.rule.startswith("SQL-000")]
    assert len(deep_warnings) == 1
    assert deep_warnings[0].file == "deep.py"
    assert any(v.rule.startswith("SQL-001") for v in result.violations)


def test_raw_sql_at_db_sink_is_flagged(
    check: SecurityPatternsCheck, tmp_path: Path
):
    # Arrange: a raw SQL string passed to db.execute.
    (tmp_path / "dao.py").write_text(_RAW_SQL_DAO, encoding="utf-8")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: exactly one SQL-001 violation, at the DAO file.
    sql_violations = [v for v in result.violations if v.rule.startswith("SQL-001")]
    assert len(sql_violations) == 1
    assert sql_violations[0].file == "dao.py"
    assert result.status == Status.FAIL


def test_parameterised_sql_is_not_flagged(
    check: SecurityPatternsCheck, tmp_path: Path
):
    # Arrange: a placeholder query with an accompanying params bag (boundary
    # case: same sink, same keywords, but safely parameterised).
    (tmp_path / "dao.py").write_text(_SAFE_SQL_DAO, encoding="utf-8")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: no SQL-001 violation is raised.
    assert not [v for v in result.violations if v.rule.startswith("SQL-001")]
    assert result.status == Status.PASS


def _write_api_endpoint(root: Path, *, source: str) -> None:
    """Write *source* to ``api/items.py`` under *root*."""
    api_dir = root / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "items.py").write_text(source, encoding="utf-8")


def test_mutation_endpoint_without_auth_is_flagged(
    check: SecurityPatternsCheck, tmp_path: Path
):
    # Arrange: a POST endpoint with no auth dependency, under the api/ layer.
    _write_api_endpoint(tmp_path, source=_UNAUTHED_ENDPOINT)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: exactly one AUTHN-001 violation, naming the endpoint file.
    authn_violations = [v for v in result.violations if v.rule.startswith("AUTHN-001")]
    assert len(authn_violations) == 1
    assert authn_violations[0].file == "api/items.py"
    assert result.status == Status.FAIL


def test_mutation_endpoint_with_auth_is_not_flagged(
    check: SecurityPatternsCheck, tmp_path: Path
):
    # Arrange: the same POST endpoint, now guarded by Depends(get_current_user).
    _write_api_endpoint(tmp_path, source=_AUTHED_ENDPOINT)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: no AUTHN-001 violation is raised.
    assert not [v for v in result.violations if v.rule.startswith("AUTHN-001")]
    assert result.status == Status.PASS


def _write_src_layout_endpoint(root: Path, *, source: str) -> None:
    """Write *source* to ``src/mypkg/api/routers/things.py`` under *root*."""
    routers = root / "src" / "mypkg" / "api" / "routers"
    routers.mkdir(parents=True)
    (routers / "things.py").write_text(source, encoding="utf-8")


def test_src_layout_endpoint_is_flagged_when_source_root_is_set(
    check: SecurityPatternsCheck, tmp_path: Path
):
    # Arrange: the regression. A src-layout project scanned from the repo root
    # puts the api/ layer at src/mypkg/api/, so the layer gate only finds it
    # once source_root anchors the walk.
    _write_src_layout_endpoint(tmp_path, source=_UNAUTHED_ENDPOINT)
    check.configure(settings={"source_root": "src/mypkg"})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the endpoint is flagged, reported at its scan-root-relative path.
    authn_violations = [v for v in result.violations if v.rule.startswith("AUTHN-001")]
    assert len(authn_violations) == 1
    assert authn_violations[0].file == "src/mypkg/api/routers/things.py"
    assert result.status == Status.FAIL


def test_src_layout_endpoint_with_auth_is_not_flagged(
    check: SecurityPatternsCheck, tmp_path: Path
):
    # Arrange: the same layout, this time with an auth dependency, so the rule
    # stays quiet rather than firing on everything it can now see.
    _write_src_layout_endpoint(tmp_path, source=_AUTHED_ENDPOINT)
    check.configure(settings={"source_root": "src/mypkg"})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert not [v for v in result.violations if v.rule.startswith("AUTHN-001")]
    assert result.status == Status.PASS


def test_source_root_does_not_widen_the_api_gate(
    check: SecurityPatternsCheck, tmp_path: Path
):
    # Arrange: the boundary. An endpoint-shaped function outside the configured
    # source root is not in the api/ layer, so it stays out of scope.
    outside = tmp_path / "scripts"
    outside.mkdir()
    (outside / "seed.py").write_text(_UNAUTHED_ENDPOINT, encoding="utf-8")
    check.configure(settings={"source_root": "src/mypkg"})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert not [v for v in result.violations if v.rule.startswith("AUTHN-001")]
    assert result.status == Status.PASS


def test_source_root_still_matches_when_scanned_from_inside_the_package(
    check: SecurityPatternsCheck, tmp_path: Path
):
    # Arrange: security_patterns is file-scoped, so a per-directory region can
    # run it rooted at src/mypkg while inheriting source_root='src/mypkg' from
    # the project config. The prefix is absent from the walk, not a mismatch.
    _write_api_endpoint(tmp_path, source=_UNAUTHED_ENDPOINT)
    check.configure(settings={"source_root": "src/mypkg"})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the endpoint is still inspected from this anchor.
    authn_violations = [v for v in result.violations if v.rule.startswith("AUTHN-001")]
    assert len(authn_violations) == 1
    assert authn_violations[0].file == "api/items.py"


def test_windows_style_source_root_is_normalised(
    check: SecurityPatternsCheck, tmp_path: Path
):
    # Arrange: source_root may be written with backslashes or wrapping slashes;
    # both normalise to the posix form the walk compares against.
    _write_src_layout_endpoint(tmp_path, source=_UNAUTHED_ENDPOINT)
    check.configure(settings={"source_root": "/src\\mypkg/"})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert [v for v in result.violations if v.rule.startswith("AUTHN-001")]
