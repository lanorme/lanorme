"""Tests for TESTFILE-001 (test_coverage): every production module under a
hardwired testable directory must have a `test_*.py` partner in
``tests/integration/``.

The check is layout-aware: it is handed ``src_root`` and treats
``src_root.parent`` as the backend root, where it looks for
``tests/integration/``. So every fixture mirrors that sibling layout::

    tmp_path/src/application/services/x.py
    tmp_path/tests/integration/test_x.py

and the check is driven directly, the same idiom as ``test_strong_types``::

    CoverageCheck().run(src_root=str(tmp_path / "src"))

It is an *advisory* (WARNING) rule and an existence check, not a coverage
measurement: a present partner, by filename or by an import in a test file,
satisfies it regardless of what the test asserts.

Tests follow AAA structure with inline ``# Arrange / # Act / # Assert`` markers.
"""

from __future__ import annotations

from pathlib import Path

from lanorme import Status
from lanorme.checks.test_coverage import TestCoverageCheck as CoverageCheck


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    """Create the src/ + tests/integration/ skeleton and return both dirs."""
    src = tmp_path / "src"
    integration = tmp_path / "tests" / "integration"
    (src / "application" / "services").mkdir(parents=True)
    integration.mkdir(parents=True)
    return src, integration


def test_uncovered_service_fires_testfile001(tmp_path: Path):
    # Arrange: a service module with no partner test anywhere.
    src, _integration = _layout(tmp_path)
    (src / "application" / "services" / "billing.py").write_text(
        "def charge(): ...\n", encoding="utf-8"
    )

    # Act.
    result = CoverageCheck().run(src_root=str(src))

    # Assert: exactly one advisory WARN, coded TESTFILE-001, at line 1, with a
    # src-relative path and a fix pointing at tests/integration/.
    assert result.status == Status.WARN
    assert len(result.warnings) == 1
    w = result.warnings[0]
    assert w.rule.startswith("TESTFILE-001")
    assert w.line == 1
    assert w.file == "src/application/services/billing.py"
    assert "billing" in w.message
    assert "tests/integration/test_billing.py" in w.fix


def test_name_matching_test_file_is_silent(tmp_path: Path):
    # Arrange: a module beside its direct test_<module>.py partner.
    src, integration = _layout(tmp_path)
    (src / "application" / "services" / "payments.py").write_text(
        "def pay(): ...\n", encoding="utf-8"
    )
    (integration / "test_payments.py").write_text(
        "def test_pay(): ...\n", encoding="utf-8"
    )

    # Act.
    result = CoverageCheck().run(src_root=str(src))

    # Assert: a name match fully covers the module; no findings, PASS.
    assert result.status == Status.PASS
    assert result.warnings == []


def test_import_in_differently_named_test_file_covers_module(tmp_path: Path):
    # Arrange: no test_billing.py, but a differently-named integration test
    # imports the module, the "by import" coverage route. A second module is
    # imported via `import ... as` to prove the alias form is also caught.
    src, integration = _layout(tmp_path)
    (src / "application" / "services" / "billing.py").write_text(
        "def charge(): ...\n", encoding="utf-8"
    )
    (src / "application" / "services" / "refunds.py").write_text(
        "def refund(): ...\n", encoding="utf-8"
    )
    (integration / "test_billing_scenarios.py").write_text(
        "from app.application.services.billing import charge\n\n"
        "def test_charge(): ...\n",
        encoding="utf-8",
    )
    (integration / "test_refunds_alias.py").write_text(
        "import app.application.services.refunds as r\n\n"
        "def test_refund(): ...\n",
        encoding="utf-8",
    )

    # Act.
    result = CoverageCheck().run(src_root=str(src))

    # Assert: both modules are considered covered via their imports; the check
    # does NOT false-positive on a partner whose filename differs.
    assert result.status == Status.PASS
    assert result.warnings == []


def test_shortened_name_partner_covers_module(tmp_path: Path):
    # Arrange: an underscore-segmented module whose partner drops the last
    # segment (user_account -> test_user.py).
    src, integration = _layout(tmp_path)
    (src / "application" / "services" / "user_account.py").write_text(
        "def f(): ...\n", encoding="utf-8"
    )
    (integration / "test_user.py").write_text(
        "def test_user(): ...\n", encoding="utf-8"
    )

    # Act.
    result = CoverageCheck().run(src_root=str(src))

    # Assert: the shortened-name route satisfies coverage; no warnings.
    assert result.status == Status.PASS
    assert result.warnings == []


def test_exempt_and_underscore_modules_never_fire(tmp_path: Path):
    # Arrange: an exempt module (session) and an underscore-prefixed module
    # (_internal), both with no test files at all.
    src, _integration = _layout(tmp_path)
    services = src / "application" / "services"
    (services / "session.py").write_text("def f(): ...\n", encoding="utf-8")
    (services / "_internal.py").write_text("def f(): ...\n", encoding="utf-8")

    # Act.
    result = CoverageCheck().run(src_root=str(src))

    # Assert: neither the exempt-set module nor the underscore module is flagged.
    assert result.status == Status.PASS
    assert result.warnings == []


def test_module_outside_testable_dirs_is_out_of_scope(tmp_path: Path):
    # Arrange: a module under domain/, which is not one of the hardwired
    # testable directories, with no test partner.
    src = tmp_path / "src"
    (src / "domain").mkdir(parents=True)
    (tmp_path / "tests" / "integration").mkdir(parents=True)
    (src / "domain" / "entity.py").write_text("def f(): ...\n", encoding="utf-8")

    # Act.
    result = CoverageCheck().run(src_root=str(src))

    # Assert: directories outside the testable set are never inspected.
    assert result.status == Status.PASS
    assert result.warnings == []


def test_partner_in_tests_unit_does_not_count(tmp_path: Path):
    # Arrange: a module whose only test lives in tests/unit/, not
    # tests/integration/.
    src = tmp_path / "src"
    (src / "api" / "v1" / "endpoints").mkdir(parents=True)
    (tmp_path / "tests" / "integration").mkdir(parents=True)
    unit = tmp_path / "tests" / "unit"
    unit.mkdir(parents=True)
    (src / "api" / "v1" / "endpoints" / "users.py").write_text(
        "def f(): ...\n", encoding="utf-8"
    )
    (unit / "test_users.py").write_text("def test_u(): ...\n", encoding="utf-8")

    # Act.
    result = CoverageCheck().run(src_root=str(src))

    # Assert: only tests/integration/ is scanned, so the module still fires.
    assert result.status == Status.WARN
    assert any(
        w.rule.startswith("TESTFILE-001") and "users" in w.message
        for w in result.warnings
    )


def test_missing_integration_dir_still_flags_modules(tmp_path: Path):
    # Arrange: production modules but no tests/integration/ directory at all.
    src = tmp_path / "src"
    (src / "application" / "commands").mkdir(parents=True)
    (src / "application" / "commands" / "create_order.py").write_text(
        "def f(): ...\n", encoding="utf-8"
    )

    # Act.
    result = CoverageCheck().run(src_root=str(src))

    # Assert: with no test files, every in-scope module is uncovered.
    assert result.status == Status.WARN
    assert any("create_order" in w.message for w in result.warnings)


def test_string_literal_substring_should_not_count_as_coverage(tmp_path: Path):
    # Arrange: order.py has no real test; a test file merely mentions the
    # module path inside a string literal.
    src, integration = _layout(tmp_path)
    (src / "application" / "services" / "order.py").write_text(
        "def f(): ...\n", encoding="utf-8"
    )
    (integration / "test_blah.py").write_text(
        "x = 'services.order is great'\n", encoding="utf-8"
    )

    # Act.
    result = CoverageCheck().run(src_root=str(src))

    # Assert: a bare string literal is not an import, so the uncovered module is
    # correctly flagged.
    assert result.status == Status.WARN
    assert any("order" in w.message for w in result.warnings)
