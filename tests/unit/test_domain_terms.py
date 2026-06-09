"""Tests for the domain_terms check (TERM-001 / TERM-NNN).

domain_terms enforces a project's canonical vocabulary across identifiers,
comments and docstrings. Matching is word-boundary anchored and
case-insensitive, and the check is INERT (always PASS) until a vocabulary is
configured -- that inertness is its false-positive guarantee.

These tests drive the check object directly via ``configure`` + ``run`` (the
same surface the CLI uses), mirroring the tmp_path idiom of test_comments.py.
Every behaviour encoded here was observed against the real check; the one known
defect (duplicate violations for a bare assignment target) is pinned with an
xfail rather than asserted as correct.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.domain_terms import DomainTermsCheck

# The canonical fixture vocabulary: prefer "Account" over the legacy synonyms.
_RULE = {"id": "TERM-001", "canonical": "Account", "forbidden": ["Acct", "Acnt"]}


@pytest.fixture
def check() -> DomainTermsCheck:
    """A domain_terms check configured with the Account vocabulary."""
    instance = DomainTermsCheck()
    instance.configure(settings={"rules": [_RULE]})
    return instance


@pytest.fixture
def inert_check() -> DomainTermsCheck:
    """A domain_terms check with no configured vocabulary (the default)."""
    instance = DomainTermsCheck()
    instance.configure(settings={})
    return instance


def _write(*, root: Path, name: str, body: str) -> Path:
    """Write a Python source file under *root* (creating parents) and return it."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _term001(result) -> list:
    """All TERM-001 violations in *result*."""
    return [v for v in result.violations if v.rule.startswith("TERM-001")]


def test_true_positive_comment_and_docstring_fire(check: DomainTermsCheck, tmp_path: Path):
    # Arrange: a forbidden term in both a docstring and an inline comment.
    _write(
        root=tmp_path,
        name="a.py",
        body='def get_account():\n    """Return the acct."""\n    # the acct lookup\n    return 1\n',
    )

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the check fails and both prose occurrences are flagged.
    assert result.status == Status.FAIL
    prose = [v for v in _term001(result) if "comment/docstring" in v.message]
    assert len(prose) == 2


def test_true_negative_canonical_term_is_silent(check: DomainTermsCheck, tmp_path: Path):
    # Arrange: code that already uses the canonical "account" everywhere.
    _write(
        root=tmp_path,
        name="b.py",
        body='def get_account():\n    """Return the account."""\n    # account lookup\n    return 1\n',
    )

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: no vocabulary drift, so the check passes.
    assert result.status == Status.PASS
    assert not result.violations


def test_inert_without_rules_never_fires(inert_check: DomainTermsCheck, tmp_path: Path):
    # Arrange: a blatant forbidden identifier, but no configured vocabulary.
    _write(root=tmp_path, name="q.py", body="acct = 1\n# acct comment\n")

    # Act.
    result = inert_check.run(src_root=str(tmp_path))

    # Assert: the check is inert by default and must not produce a false positive.
    assert result.status == Status.PASS
    assert not result.violations


def test_empty_forbidden_list_is_inert(tmp_path: Path):
    # Arrange: a rule whose forbidden list is empty contributes no patterns.
    instance = DomainTermsCheck()
    instance.configure(settings={"rules": [{"id": "T", "canonical": "X", "forbidden": []}]})
    _write(root=tmp_path, name="e.py", body="acct = 1\n")

    # Act.
    result = instance.run(src_root=str(tmp_path))

    # Assert: a rule with nothing forbidden behaves like no rule at all.
    assert result.status == Status.PASS
    assert not result.violations


def test_forbidden_term_inside_regular_string_literal_is_silent(
    check: DomainTermsCheck, tmp_path: Path
):
    # Arrange: the forbidden term appears only inside non-docstring string
    # literals (an assigned string and a non-first bare string).
    body = (
        'X = "acct in assigned string should be silent"\n'
        "\n"
        "def f():\n"
        "    x = 1\n"
        '    "acct in a non-first bare string"\n'
        "    return x\n"
    )
    _write(root=tmp_path, name="z.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: string literals are outside the scanned surface -- firing here
    # would be a false positive, the cardinal sin.
    assert result.status == Status.PASS
    assert not result.violations


def test_substring_overlap_does_not_false_positive(check: DomainTermsCheck, tmp_path: Path):
    # Arrange: identifiers that merely CONTAIN the forbidden term as a substring.
    _write(root=tmp_path, name="wb.py", body="acctual = 3\nxacctx = 4\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: word-boundary anchoring means incidental overlap is not a match.
    assert result.status == Status.PASS
    assert not result.violations


def test_compound_identifier_is_not_matched(check: DomainTermsCheck, tmp_path: Path):
    # Arrange: compound forms where the forbidden term abuts \\w (underscore or a
    # camelCase letter), so the word boundary never lands.
    _write(root=tmp_path, name="cmp.py", body="AcctId = 1\nget_acct_id = 2\nacct_balance = 5\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: pins the OBSERVED (documented) word-boundary behaviour -- these
    # compound forms currently slip through (see findings: intent_question).
    assert result.status == Status.PASS
    assert not result.violations


def test_bare_identifier_class_name_fires_once(check: DomainTermsCheck, tmp_path: Path):
    # Arrange: a class whose name is exactly the forbidden term (clean single
    # node, unlike an assignment which is double-counted).
    _write(root=tmp_path, name="c.py", body="class Acct:\n    pass\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: exactly one identifier-level violation, naming the matched term.
    assert result.status == Status.FAIL
    ident = [v for v in _term001(result) if "identifier" in v.message]
    assert len(ident) == 1
    assert "Acct" in ident[0].rule


def test_import_line_comment_skipped_normal_comment_fires(
    check: DomainTermsCheck, tmp_path: Path
):
    # Arrange: the forbidden term in an import-line comment and in a normal one.
    _write(
        root=tmp_path,
        name="i.py",
        body="import os  # acct in import-line comment\nx = 1  # acct in normal comment\n",
    )

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: only the non-import comment (line 2) is flagged.
    prose = [v for v in _term001(result) if "comment/docstring" in v.message]
    assert len(prose) == 1
    assert prose[0].line == 2


def test_regex_special_chars_in_forbidden_are_escaped(tmp_path: Path):
    # Arrange: a forbidden term containing a regex metacharacter ('.').
    instance = DomainTermsCheck()
    instance.configure(
        settings={"rules": [{"id": "TERM-002", "canonical": "User", "forbidden": ["U.ser"]}]}
    )
    _write(root=tmp_path, name="r.py", body="# U.ser here and User here\nx = 1\n")

    # Act.
    result = instance.run(src_root=str(tmp_path))

    # Assert: the dot matches literally (one hit on 'U.ser'), and the wildcard
    # interpretation that would also match 'User' does NOT occur.
    hits = [v for v in result.violations if v.rule.startswith("TERM-002")]
    assert len(hits) == 1
    assert "U.ser" in hits[0].message


def test_test_prefixed_and_migrations_files_are_exempt(check: DomainTermsCheck, tmp_path: Path):
    # Arrange: blatant violations in an exempt test_ file and a migrations/ file,
    # beside a non-exempt module.
    _write(root=tmp_path, name="test_thing.py", body="acct = 1\n")
    _write(root=tmp_path, name="migrations/0001.py", body="acct = 1\n")
    _write(root=tmp_path, name="real.py", body="acct = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: only the non-exempt file is flagged.
    files = {v.file for v in _term001(result)}
    assert files == {"real.py"}


@pytest.mark.xfail(
    reason="Known defect: a bare assignment target is visited as both ast.Assign "
    "and ast.Name, so `acct = 1` emits two identical violations instead of one.",
    strict=True,
)
def test_bare_assignment_should_fire_exactly_once(check: DomainTermsCheck, tmp_path: Path):
    # Arrange: a single bare assignment to the forbidden identifier.
    _write(root=tmp_path, name="dup.py", body="acct = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: ideally one violation per textual occurrence (currently emits two).
    ident = [v for v in _term001(result) if "identifier" in v.message]
    assert len(ident) == 1
