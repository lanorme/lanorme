"""Every test-aware check agrees on what a test file is.

Four boundary files exercise the shared definition (``lanorme.paths``)
through each check's ``check(scan)``:

- ``tests/helpers.py``: under a tests directory, no ``test_`` prefix.
- ``pkg/test_module.py``: a ``test_*.py`` module beside production code.
- ``pkg/conftest.py``: pytest support outside any tests directory.
- ``tests/fixtures/data.py``: a fixture module under ``tests/fixtures/``.

A production rule must skip all four and still flag ``pkg/service.py``; a
rule that judges tests (``test_style``) must judge only the collected module;
the coverage partner lookup must count only collected modules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme.checks.attribute_access import AttributeAccessCheck
from lanorme.checks.docstrings import DocstringsCheck
from lanorme.checks.domain_terms import DomainTermsCheck
from lanorme.checks.duplication import DuplicationCheck
from lanorme.checks.file_limits import FileLimitsCheck
from lanorme.checks.named_args import NamedArgsCheck
from lanorme.checks.naming_scope import NamingScopeCheck
from lanorme.checks.pattern_divergence import PatternDivergenceCheck
from lanorme.checks.secrets import SecretsCheck
from lanorme.checks.security_patterns import SecurityPatternsCheck
from lanorme.checks.similarity import SimilarityCheck
from lanorme.checks.stale_paths import StalePathsCheck
from lanorme.checks.strong_types import StrongTypesCheck
from lanorme.checks.test_coverage import TestCoverageCheck as CoverageCheck
from lanorme.checks.test_style import TestStyleCheck as StyleCheck
from lanorme.scan import Scan

BOUNDARY_FILES = (
    "tests/helpers.py",
    "pkg/test_module.py",
    "pkg/conftest.py",
    "tests/fixtures/data.py",
)
PRODUCTION_FILE = "pkg/service.py"

_DUP_PAIR = "".join(
    f"def {name}(a, b):\n"
    "    total = 0\n"
    "    total = a + b\n"
    "    total = total + 1\n"
    "    total = total - 0\n"
    "    return total\n\n"
    for name in ("alpha", "beta")
)
_CLONE_PAIR = (
    "def a(s):\n x = s.alpha\n y = s.beta\n z = combine(x, y)\n w = z * 2\n return w\n\n"
    "def b(s):\n x = s.gamma\n y = s.beta\n z = combine(x, y)\n w = z * 2\n return w\n"
)
_LONG_SPAN = (
    "def sample(rows):\n"
    '    """Walk the rows and accumulate."""\n'
    "    rc = 0\n"
    "    total = 0\n"
    + "".join(f"    total += {i} - {i}\n" for i in range(40))
    + "    return rc + total\n"
)


def _build_configured(check, settings: dict):
    check.configure(settings=settings)
    return check


# One (check factory, triggering source) pair per production rule that
# exempts test code. Each source fires the rule on a plain module.
PRODUCTION_RULES = {
    "named_args": (
        lambda: _build_configured(NamedArgsCheck(), {"enabled": True}),
        "def f(a, b):\n    return a\n",
    ),
    "duplication": (DuplicationCheck, _DUP_PAIR),
    "file_limits": (FileLimitsCheck, "def f(a, b, c, d, e, g, h, i, j):\n    return a\n"),
    "similarity": (lambda: _build_configured(SimilarityCheck(), {"enabled": True}), _CLONE_PAIR),
    "secrets": (SecretsCheck, 'password = "s3cr3t-prod-value"\n'),
    "security_patterns": (
        SecurityPatternsCheck,
        "def fetch(db):\n    return db.execute(\"SELECT id FROM users WHERE name = 'bob'\")\n",
    ),
    "pattern_divergence": (PatternDivergenceCheck, "def f():\n    import os\n    return os\n"),
    "naming_scope": (lambda: _build_configured(NamingScopeCheck(), {"enabled": True}), _LONG_SPAN),
    "strong_types": (
        StrongTypesCheck,
        "from typing import Any\n\n\ndef f(x: dict[str, Any]) -> None:\n    return None\n",
    ),
    "stale_paths": (
        lambda: _build_configured(StalePathsCheck(), {"tokens": ["old_pkg/"]}),
        "# moved from old_pkg/legacy.py\nX = 1\n",
    ),
    "attribute_access": (
        lambda: _build_configured(AttributeAccessCheck(), {"enabled": True}),
        "def f(x):\n    return hasattr(x, 'foo')\n",
    ),
    "docstrings": (
        lambda: _build_configured(DocstringsCheck(), {"enabled": True}),
        "def calculate_total(items):\n    first = 1\n    second = 2\n    third = 3\n    return first + second + third\n",
    ),
    "domain_terms": (
        lambda: _build_configured(
            DomainTermsCheck(),
            {"rules": [{"id": "TERM-001", "canonical": "Account", "forbidden": ["Acct"]}]},
        ),
        'def get_account():\n    """Return the acct."""\n    return 1\n',
    ),
}

_UNMARKED_TEST = "def test_thing():\n    a = 1\n    b = 2\n    c = a + b\n    assert c == 3\n"


def _write_layout(root: Path, *, source: str) -> None:
    """Write *source* to every boundary file and the production file under *root*."""
    for relative in (*BOUNDARY_FILES, PRODUCTION_FILE):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _collect_flagged_files(result) -> set[str]:
    """Files carrying a real finding (the ``-000`` skip notices are not findings)."""
    findings = [*result.violations, *result.warnings]
    return {
        v.file.replace("\\", "/") for v in findings if not v.rule.split(":")[0].endswith("-000")
    }


@pytest.mark.parametrize("check_name", sorted(PRODUCTION_RULES))
def test_production_rule_skips_every_test_file_shape(tmp_path: Path, check_name: str) -> None:
    # Arrange: the same triggering source in the four boundary files and one production file.
    factory, source = PRODUCTION_RULES[check_name]
    _write_layout(tmp_path, source=source)

    # Act
    result = factory().check(Scan(root=tmp_path))

    # Assert: only the production file is flagged, so the rule fires and every test shape is exempt.
    assert _collect_flagged_files(result) == {PRODUCTION_FILE}


def test_test_style_judges_only_the_collected_module(tmp_path: Path) -> None:
    # Arrange: an unmarked test function in every boundary file and the production file.
    _write_layout(tmp_path, source=_UNMARKED_TEST)
    check = _build_configured(StyleCheck(), {"enabled": True})

    # Act
    result = check.check(Scan(root=tmp_path))

    # Assert: helpers, conftest and fixtures are never judged as tests, even under tests/.
    assert _collect_flagged_files(result) == {"pkg/test_module.py"}


def _run_coverage(tmp_path: Path, *, partner: str, body: str) -> list:
    """Lay out one endpoint module plus *partner* under tests/integration; return the TESTFILE-001 hits."""
    src = tmp_path / "src"
    (src / "api" / "v1" / "endpoints").mkdir(parents=True)
    (src / "api" / "v1" / "endpoints" / "users.py").write_text("def f(): ...\n", encoding="utf-8")
    path = tmp_path / partner
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    result = CoverageCheck().check(Scan(root=src))
    return [w for w in result.warnings if w.rule.startswith("TESTFILE-001")]


@pytest.mark.parametrize(
    "partner",
    [
        "tests/integration/helpers.py",
        "tests/integration/conftest.py",
        "tests/integration/fixtures/users.py",
    ],
)
def test_coverage_does_not_credit_support_files_as_partners(tmp_path: Path, partner: str) -> None:
    # Arrange / Act: the only file importing the module is test support, not a collected test.
    hits = _run_coverage(tmp_path, partner=partner, body="from endpoints.users import f\n")

    # Assert: the module is still reported as untested.
    assert len(hits) == 1


@pytest.mark.parametrize(
    "partner",
    ["tests/integration/api/test_users.py", "tests/integration/users_test.py"],
)
def test_coverage_credits_collected_modules_anywhere_under_the_root(
    tmp_path: Path,
    partner: str,
) -> None:
    # Arrange / Act: the partner is a nested test module, or a *_test.py module, under the root.
    hits = _run_coverage(tmp_path, partner=partner, body="def test_f(): ...\n")

    # Assert: found by stem, whichever pytest naming shape and depth it uses.
    assert hits == []
