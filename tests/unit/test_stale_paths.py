"""Tests for the stale_paths check (STALE-001).

STALE-001 flags configured "stale path tokens" lingering in Python docstrings
and inline comments. It is inert until configured. Precision matters: a path-
like token living inside a STRING LITERAL (not a docstring, not a comment) must
NOT fire.

The check is configured directly via ``StalePathsCheck.configure(settings=...)``
rather than through the CLI/TOML layer, mirroring the unit-test idiom of calling
the check object directly with ``tmp_path`` fixtures.

A regression test pins the precision contract: when a ``#`` appears inside a
string literal, the comment scanner (driven by :mod:`tokenize`) must not treat
the rest of the line as a comment, so a stale token there stays silent.
"""

from __future__ import annotations

from pathlib import Path

from lanorme import Status
from lanorme.checks.stale_paths import StalePathsCheck


def _write(*, root: Path, name: str, body: str) -> None:
    """Write a Python source file under *root*."""
    (root / name).write_text(body, encoding="utf-8")


def _configured(*, tokens: list[str]) -> StalePathsCheck:
    """A stale_paths check configured with the given stale tokens."""
    check = StalePathsCheck()
    check.configure(settings={"tokens": tokens})
    return check


def test_inert_when_unconfigured(tmp_path: Path):
    # Arrange: an unconfigured check and a file that *would* violate if configured.
    check = StalePathsCheck()
    _write(root=tmp_path, name="m.py", body='"""See `old_pkg/foo.py`."""\n')

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: no tokens configured -> the check is inert and always passes.
    assert result.status == Status.PASS
    assert result.violations == []


def test_flags_stale_token_in_comment(tmp_path: Path):
    # Arrange: a plain ``old_pkg/legacy.py`` reference inside an inline comment.
    check = _configured(tokens=["old_pkg/"])
    _write(root=tmp_path, name="c.py", body="# see old_pkg/legacy.py for details\nx = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: a single STALE-001 'in comment' finding.
    assert result.status == Status.FAIL
    hits = [v for v in result.violations if v.rule == "STALE-001"]
    assert len(hits) == 1
    assert "old_pkg/legacy.py" in hits[0].message
    assert "in comment" in hits[0].message


def test_flags_backtick_and_plain_in_module_docstring(tmp_path: Path):
    # Arrange: a backtick-wrapped stale ``.py`` path in a module docstring. Both
    # the backtick pattern and the plain ``.py`` pattern match the same text.
    check = _configured(tokens=["old_pkg/"])
    _write(root=tmp_path, name="d.py", body='"""Module references `old_pkg/foo.py`."""\nx = 1\n')

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the reference is reported twice (backtick form + plain form), both
    # on the docstring line. This pins the observed double-report behaviour.
    assert result.status == Status.FAIL
    hits = [v for v in result.violations if v.rule == "STALE-001"]
    assert len(hits) == 2
    messages = sorted(v.message for v in hits)
    assert messages == [
        "Stale path reference '`old_pkg/foo.py`'",
        "Stale path reference 'old_pkg/foo.py'",
    ]


def test_flags_class_and_function_docstrings(tmp_path: Path):
    # Arrange: stale tokens in both a class docstring and a method docstring.
    check = _configured(tokens=["old_pkg/"])
    body = (
        "class Foo:\n"
        '    """Uses `old_pkg/bar.py`."""\n'
        "    def m(self):\n"
        '        """Refers to old_pkg/baz.py."""\n'
        "        return 0\n"
    )
    _write(root=tmp_path, name="cls.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the class docstring backtick path double-reports; the method
    # docstring plain path reports once -> three findings total.
    assert result.status == Status.FAIL
    hits = [v for v in result.violations if v.rule == "STALE-001"]
    assert len(hits) == 3
    assert any("old_pkg/baz.py" in v.message for v in hits)


def test_string_literal_without_hash_does_not_fire(tmp_path: Path):
    # Arrange: a stale-looking path inside an ordinary assignment string. It is
    # neither a comment (no '#') nor a first-statement docstring.
    check = _configured(tokens=["old_pkg/"])
    _write(
        root=tmp_path,
        name="s.py",
        body='"""Clean docstring."""\nPATH = "old_pkg/runtime.py"\n',
    )

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: precision-first -> string literals must stay silent.
    assert result.status == Status.PASS
    assert result.violations == []


def test_bare_string_expression_is_not_treated_as_docstring(tmp_path: Path):
    # Arrange: a bare string expression that is NOT the first statement, so it is
    # not a docstring; it also contains no '#'.
    check = _configured(tokens=["old_pkg/"])
    _write(root=tmp_path, name="nd.py", body='x = 1\n"`old_pkg/foo.py`"\n')

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: only node.body[0] is inspected for docstrings -> no firing.
    assert result.status == Status.PASS
    assert result.violations == []


def test_word_boundary_prevents_substring_match(tmp_path: Path):
    # Arrange: 'mysrc/thing.py' must not match the token 'src/' (no word
    # boundary before 's' in 'mysrc').
    check = _configured(tokens=["src/"])
    _write(root=tmp_path, name="b.py", body="# mention of mysrc/thing.py\na = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the leading word char defeats the \\b anchor -> silent.
    assert result.status == Status.PASS
    assert result.violations == []


def test_plain_directory_token_without_py_does_not_fire(tmp_path: Path):
    # Arrange: a comment naming a directory token without a trailing '.py' file.
    # Only the plain '.py' pattern (not the backtick form) applies in a comment.
    check = _configured(tokens=["old_pkg/"])
    _write(root=tmp_path, name="dir.py", body="# the old_pkg directory layout\na = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: 'old_pkg' (no slash+ident+.py) does not satisfy either pattern.
    assert result.status == Status.PASS
    assert result.violations == []


def test_tests_directory_is_exempt_on_whole_project_scan(tmp_path: Path):
    # Arrange: a violating file under tests/ plus a violating file at the root,
    # scanned from the project root so tests/ keeps its relative prefix.
    check = _configured(tokens=["old_pkg/"])
    (tmp_path / "tests").mkdir()
    _write(root=tmp_path / "tests", name="test_x.py", body="# old_pkg/legacy.py\n")
    _write(root=tmp_path, name="prod.py", body="# old_pkg/legacy.py\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the tests/ file is skipped; only the production file is flagged.
    flagged_files = {v.file for v in result.violations}
    assert "prod.py" in flagged_files
    assert not any(f.replace("\\", "/").startswith("tests/") for f in flagged_files)


def test_syntax_error_file_is_skipped_silently(tmp_path: Path):
    # Arrange: a file that cannot be parsed (the AST docstring walk would raise).
    check = _configured(tokens=["old_pkg/"])
    _write(root=tmp_path, name="broken.py", body="def f(:\n    # old_pkg/x.py\n")

    # Act: the per-file parse guard swallows SyntaxError.
    result = check.run(src_root=str(tmp_path))

    # Assert: an unparseable file yields no violations and no crash.
    assert result.status == Status.PASS
    assert result.violations == []


def test_hash_inside_string_literal_must_not_fire(tmp_path: Path):
    # Arrange: a '#' inside a dict value string, with a stale token later on the
    # same line. There is no real comment here.
    check = _configured(tokens=["old_pkg/"])
    _write(
        root=tmp_path,
        name="hash.py",
        body='d = {"sep": "#", "path": "old_pkg/foo.py"}\n',
    )

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert (desired contract): no firing inside string literals.
    assert result.status == Status.PASS
    assert result.violations == []