"""Tests for the naming_clean_code check (NAMING-009..011, opt-in).

The check is default-off; every behavioural test enables it through
``configure``. NAMING-011 takes only the queries, since a noun-named command is
NAMING-007's finding in the default-on check, so the suite pins that boundary
as well as the exemptions (predicates, constructors, conversions, properties).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.naming_clean_code import NamingCleanCodeCheck


@pytest.fixture
def check() -> NamingCleanCodeCheck:
    """An enabled naming_clean_code check (it is default-off otherwise)."""
    instance = NamingCleanCodeCheck()
    instance.configure(settings={"enabled": True})
    return instance


def _run(*, root: Path, body: str, check: NamingCleanCodeCheck, name: str = "sample.py"):
    """Write *body* as *name* under *root* and run *check* over it."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return check.run(src_root=str(root))


def _codes(result) -> list[str]:
    """The rule codes of all warnings on *result*."""
    return [w.code for w in result.warnings]


# --------------------------------------------------------------------------- #
# Default-off
# --------------------------------------------------------------------------- #


def test_disabled_by_default(tmp_path: Path) -> None:
    result = _run(root=tmp_path, body="class UserManager:\n    pass\n", check=NamingCleanCodeCheck())
    assert result.status is Status.PASS and result.warnings == []


# --------------------------------------------------------------------------- #
# NAMING-009: noise words
# --------------------------------------------------------------------------- #


def test_noise_word_classes_are_flagged(tmp_path: Path, check: NamingCleanCodeCheck) -> None:
    result = _run(root=tmp_path, body="class UserManager:\n    pass\nclass ConfigData:\n    pass\n", check=check)
    assert _codes(result) == ["NAMING-009", "NAMING-009"]
    assert result.violations == [] and result.status is Status.WARN


def test_noise_word_exemptions(tmp_path: Path, check: NamingCleanCodeCheck) -> None:
    body = "".join(
        f"class {name}:\n    pass\n"
        for name in ("MetaData", "ResultMetaData", "FileContextManager", "Manager", "CONSOLE_INFO", "MetaInfo")
    )
    result = _run(root=tmp_path, body=body, check=check)
    assert [(w.code, w.line) for w in result.warnings] == [("NAMING-009", 11)]


def test_exempt_covers_noise_words_and_junk_modules(tmp_path: Path) -> None:
    # Arrange
    check = NamingCleanCodeCheck()
    check.configure(settings={"enabled": True, "exempt": ["UserManager", "utils"]})

    # Act
    result = _run(root=tmp_path, body="class UserManager:\n    pass\n", check=check, name="utils.py")

    # Assert
    assert _codes(result) == []


# --------------------------------------------------------------------------- #
# NAMING-010: junk-drawer modules
# --------------------------------------------------------------------------- #


def test_junk_module_and_package_are_flagged(tmp_path: Path, check: NamingCleanCodeCheck) -> None:
    # Arrange: a module and a package named for having no name.
    (tmp_path / "helpers").mkdir()
    (tmp_path / "helpers" / "__init__.py").write_text("", encoding="utf-8")

    # Act
    result = _run(root=tmp_path, body="", check=check, name="utils.py")

    # Assert: whole-file findings on line 0, the package reported as one.
    assert sorted((w.code, w.file, w.line) for w in result.warnings) == [
        ("NAMING-010", "helpers/__init__.py", 0),
        ("NAMING-010", "utils.py", 0),
    ]
    assert "Package 'helpers'" in result.warnings[0].message


def test_named_module_passes(tmp_path: Path, check: NamingCleanCodeCheck) -> None:
    result = _run(root=tmp_path, body="", check=check, name="paths.py")
    assert _codes(result) == []


# --------------------------------------------------------------------------- #
# NAMING-011: every function starts with a verb
# --------------------------------------------------------------------------- #


def test_query_without_a_verb_is_flagged(tmp_path: Path, check: NamingCleanCodeCheck) -> None:
    result = _run(root=tmp_path, body="def _shell_violations(tree):\n    return []\n", check=check)
    assert _codes(result) == ["NAMING-011"]
    assert "find_" in result.warnings[0].fix


def test_queries_with_a_verb_or_a_predicate_pass(tmp_path: Path, check: NamingCleanCodeCheck) -> None:
    # Arrange: verb-first, predicates, a constructor, a property, conversions, a protocol method.
    body = (
        "def find_shell_violations(tree):\n    return []\n"
        "def line_has_noqa(line):\n    return True\n"
        "def is_valid():\n    return True\n"
        "def matches_pattern(s):\n    return True\n"
        "class Config:\n"
        "    @classmethod\n    def of(cls):\n        return cls()\n"
        "    @property\n    def name(self):\n        return 1\n"
        "    def keys(self):\n        return []\n"
        "def from_dict(d):\n    return d\n"
        "def with_capacity(n):\n    return n\n"
        "def dict_from_rows(r):\n    return r\n"
    )

    # Act
    result = _run(root=tmp_path, body=body, check=check)

    # Assert
    assert _codes(result) == []


def test_commands_and_raisers_are_not_queries(tmp_path: Path, check: NamingCleanCodeCheck) -> None:
    body = "def layout(root):\n    root.write_text('x')\ndef key_not_found(key):\n    raise KeyError(key)\n"
    result = _run(root=tmp_path, body=body, check=check)
    assert _codes(result) == []


def test_query_fix_puts_a_later_verb_first(tmp_path: Path, check: NamingCleanCodeCheck) -> None:
    result = _run(root=tmp_path, body="def _cert_verify_result(conn):\n    return conn\n", check=check)
    assert "'_verify_cert_result'" in result.warnings[0].fix


def test_verbs_and_exempt_config(tmp_path: Path) -> None:
    # Arrange
    check = NamingCleanCodeCheck()
    check.configure(settings={"enabled": True, "verbs": ["frob"], "exempt": ["url_for"]})
    body = "def frob_all():\n    return 1\ndef url_for(x):\n    return x\ndef thresholds():\n    return 1\n"

    # Act
    result = _run(root=tmp_path, body=body, check=check)

    # Assert: only the unconfigured query remains.
    assert [(w.code, w.line) for w in result.warnings] == [("NAMING-011", 5)]
