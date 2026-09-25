"""Tests for the comments check (CMT-001, CMT-002) and its parse guard.

The regression of note: ``_comment_parses_as_code`` runs ``ast.parse`` on text
built from a comment and previously caught only ``(SyntaxError, ValueError)``.
A deeply nested but parseable expression in a single comment can overflow the
parser and raise ``RecursionError``, crashing the whole check. Such a comment
must be treated as prose (not commented-out code), never fatal.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lanorme import Status
from lanorme import comment_code as comments_module
from lanorme.comment_code import _comment_parses_as_code
from lanorme.checks.comments import CommentsCheck

# A single-line expression nested far enough to overflow a recursion-bounded
# parser, yet syntactically valid where it does parse.
_DEEP_EXPRESSION = "(" + "[" * 200 + "]" * 200 + ")"


@pytest.fixture
def check() -> CommentsCheck:
    """A comments check with default thresholds (block 6 lines, line 120 chars)."""
    return CommentsCheck()


def _write(*, root: Path, name: str, body: str) -> None:
    """Write a Python source file under *root*."""
    (root / name).write_text(body, encoding="utf-8")


def test_deep_comment_expression_does_not_crash(check: CommentsCheck, tmp_path: Path):
    # Arrange: force ``ast.parse`` to raise RecursionError as a recursion-bounded
    # build would, then feed the helper a deep expression directly.
    def _boom(_source: str) -> ast.Module:
        raise RecursionError("maximum recursion depth exceeded")

    # Act + Assert: the guard swallows it and reports "not code", never raising.
    monkeypatched = comments_module.ast.parse
    comments_module.ast.parse = _boom
    try:
        result = _comment_parses_as_code(_DEEP_EXPRESSION)
    finally:
        comments_module.ast.parse = monkeypatched

    assert result is False


def test_deep_comment_in_file_completes_without_flagging(check: CommentsCheck, tmp_path: Path):
    # Arrange: a real file whose comment is a deep parseable expression, beside
    # ordinary code. On any interpreter the run must finish.
    _write(root=tmp_path, name="deep.py", body=f"# {_DEEP_EXPRESSION}\nx = 1\n")

    # Act: the run must complete rather than raise RecursionError.
    result = check.run(src_root=str(tmp_path))

    # Assert: the deep comment is not mistaken for commented-out code (it may
    # still trip the unrelated over-long-line rule, which is fine here).
    assert result.check == "comments"
    assert not any(v.rule == "CMT-001" for v in result.violations)


def test_cmt001_flags_commented_out_code(check: CommentsCheck, tmp_path: Path):
    # Arrange: a standalone comment that is really an assignment statement.
    _write(root=tmp_path, name="dead.py", body="x = 1\n# y = x + 2\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    assert any(v.rule == "CMT-001" for v in result.violations)


def test_cmt001_ignores_prose(check: CommentsCheck, tmp_path: Path):
    # Arrange: a sentence-shaped comment that is prose, not code.
    _write(root=tmp_path, name="prose.py", body="# this is a real explanation.\nx = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.violations


_PEP723_BLOCK = (
    '# /// script\n# requires-python = ">=3.13"\n# dependencies = ["requests", "rich"]\n# ///\n'
)


def test_cmt001_skips_pep723_inline_metadata(check: CommentsCheck, tmp_path: Path):
    # Arrange: a PEP 723 block whose `dependencies = [...]` line parses as an
    # assignment, beside ordinary code.
    _write(root=tmp_path, name="script.py", body=f"{_PEP723_BLOCK}import sys\n\nprint(sys.argv)\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the metadata block is tooling, not commented-out code.
    assert not any(v.rule == "CMT-001" for v in result.violations)


def test_cmt001_still_flags_dead_code_outside_pep723_block(check: CommentsCheck, tmp_path: Path):
    # Arrange: real commented-out code follows a valid PEP 723 block; the skip
    # must be scoped to the block, not the rest of the file.
    _write(
        root=tmp_path,
        name="script.py",
        body=f"{_PEP723_BLOCK}import sys\n\n# y = sys.argv[0]\n",
    )

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the block is exempt, the trailing dead-code line is not.
    flagged = [v for v in result.violations if v.rule == "CMT-001"]
    assert len(flagged) == 1
    assert "y = sys.argv[0]" in flagged[0].message


def test_pep723_metadata_lines_requires_a_closing_fence():
    # Arrange: an opener with no `# ///` close is not a metadata block.
    lines = ["# /// script", '# dependencies = ["rich"]', "import sys"]

    # Act.
    metadata = comments_module._find_pep723_metadata_lines(lines)

    # Assert: nothing is treated as metadata, so the deps line stays lintable.
    assert metadata == frozenset()


def test_cmt002_flags_overlong_block(check: CommentsCheck, tmp_path: Path):
    # Arrange: seven consecutive standalone comment lines, one past the limit of six.
    block = "".join(f"# note number {i}\n" for i in range(7))
    _write(root=tmp_path, name="block.py", body=block + "x = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    block_hits = [v for v in result.violations if v.rule == "CMT-002" and "block" in v.message]
    assert len(block_hits) == 1
    assert "7 lines" in block_hits[0].message


def test_cmt002_block_at_limit_is_allowed(check: CommentsCheck, tmp_path: Path):
    # Arrange: a comment block of exactly the limit (six lines) is the boundary.
    block = "".join(f"# note number {i}\n" for i in range(6))
    _write(root=tmp_path, name="edge.py", body=block + "x = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not any(v.rule == "CMT-002" for v in result.violations)


def test_cmt002_flags_overlong_line(check: CommentsCheck, tmp_path: Path):
    # Arrange: a single comment whose text exceeds the 120-character limit.
    long_text = "x" * 130
    _write(root=tmp_path, name="long.py", body=f"# {long_text}\nvalue = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    line_hits = [v for v in result.violations if v.rule == "CMT-002" and "chars" in v.message]
    assert len(line_hits) == 1
    assert "130 chars" in line_hits[0].message


# --------------------------------------------------------------------------- #
# CMT-002: the block allowance scales with the complexity it explains
# --------------------------------------------------------------------------- #


def _build_branchy_body(*, arms: int) -> str:
    """A function body with *arms* decision points, to drive up complexity."""
    return "".join(f"    if value == {i} and value > 0:\n        return {i}\n" for i in range(arms))


def test_cmt002_long_block_allowed_in_front_of_complex_code(check: CommentsCheck, tmp_path: Path):
    # Arrange: ten lines of explanation introducing a function COMPLEXITY-001 would warn about.
    block = "".join(f"# explanation line {i}\n" for i in range(10))
    body = f"{block}def hard(value):\n{_build_branchy_body(arms=12)}    return 0\n"
    _write(root=tmp_path, name="hard.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: hard code earns the room to say why it is hard.
    assert not any(v.rule == "CMT-002" for v in result.violations)


def test_cmt002_same_block_still_flagged_in_front_of_trivial_code(
    check: CommentsCheck,
    tmp_path: Path,
):
    # Arrange: the identical block, this time introducing a one-line function.
    block = "".join(f"# explanation line {i}\n" for i in range(10))
    body = f"{block}def easy(value):\n    return value\n"
    _write(root=tmp_path, name="easy.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    hits = [v for v in result.violations if v.rule == "CMT-002" and "lines" in v.message]
    assert len(hits) == 1
    assert "complexity 1" in hits[0].message


def test_cmt002_block_inside_a_complex_function_is_allowed(check: CommentsCheck, tmp_path: Path):
    # Arrange: the explanation sits in the middle of the hard function, not above it.
    block = "".join(f"    # explanation line {i}\n" for i in range(10))
    body = f"def hard(value):\n{_build_branchy_body(arms=12)}{block}    return 0\n"
    _write(root=tmp_path, name="inside.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert not any(v.rule == "CMT-002" for v in result.violations)


def test_cmt002_module_banner_keeps_the_base_allowance(check: CommentsCheck, tmp_path: Path):
    # Arrange: a block far from any definition must not inherit a function's allowance.
    block = "".join(f"# banner line {i}\n" for i in range(10))
    body = f"def hard(value):\n{_build_branchy_body(arms=12)}    return 0\n\n\n{block}\n\n\nVALUE = 1\n"
    _write(root=tmp_path, name="banner.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    assert any("complexity 1" in v.message for v in result.violations if v.rule == "CMT-002")


def test_cmt002_scaling_is_configurable(tmp_path: Path):
    # Arrange: turning the per-branch allowance off restores the old flat cap.
    instance = CommentsCheck()
    instance.configure(settings={"block_lines_per_branch": 0})
    block = "".join(f"# explanation line {i}\n" for i in range(10))
    _write(
        root=tmp_path,
        name="flat.py",
        body=f"{block}def hard(value):\n{_build_branchy_body(arms=12)}    return 0\n",
    )

    # Act.
    result = instance.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    assert any(v.rule == "CMT-002" for v in result.violations)


def test_root_under_a_skip_named_ancestor_is_still_scanned(check: CommentsCheck, tmp_path: Path):
    # Arrange: commented-out code in a project checked out under a build/ dir.
    root = tmp_path / "build" / "project"
    root.mkdir(parents=True)
    _write(root=root, name="dead.py", body="x = 1\n# y = x + 2\n")

    # Act: scan the project, not its ancestor.
    result = check.run(src_root=str(root))

    # Assert: the ancestor is the user's filesystem, not the project layout.
    assert result.status == Status.FAIL
    assert any(v.rule == "CMT-001" for v in result.violations)


# --------------------------------------------------------------------------- #
# CMT-001: shapes that parse as Python but are not code
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "TODO: retries = 5",
        "default: timeout = 30",
        "cython: boundscheck=False",
        "enabled = true",
        "timeout = null",
        "return early",
        "import lazily",
        "raise instead",
    ],
)
def test_cmt001_ignores_notes_that_happen_to_parse(
    check: CommentsCheck,
    tmp_path: Path,
    text: str,
):
    # Arrange: a labelled note, a foreign literal or an adverb after a keyword.
    _write(root=tmp_path, name="note.py", body=f"# {text}\nx = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: none of these is disabled code.
    assert not any(v.rule == "CMT-001" for v in result.violations)


@pytest.mark.parametrize(
    "text",
    [
        "x: int = 5",
        "items: list[int] = []",
        "result: Result = compute()",
        "return result",
        "return reply",
        "import os",
    ],
)
def test_cmt001_still_flags_typed_assignments_and_real_operands(
    check: CommentsCheck,
    tmp_path: Path,
    text: str,
):
    # Arrange: the same shapes with a real type or a real name.
    _write(root=tmp_path, name="dead.py", body=f"# {text}\nx = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert [v.line for v in result.violations if v.rule == "CMT-001"] == [1]


def test_cmt001_ignores_code_under_an_example_header(check: CommentsCheck, tmp_path: Path):
    # Arrange: an illustration block, then a separate block of disabled code.
    body = (
        "# Typical usage:\n"
        '#     register("svc", timeout=30)\n'
        "#     client.get(url)\n"
        "x = 1\n"
        "# Old code:\n"
        "#     total = add_legacy(1, 2)\n"
    )
    _write(root=tmp_path, name="usage.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: only the block without an illustration header is dead code.
    assert [v.line for v in result.violations if v.rule == "CMT-001"] == [6]


# --------------------------------------------------------------------------- #
# CMT-002: blocks and lines that are long for a reason
# --------------------------------------------------------------------------- #


def test_cmt002_licence_header_is_not_a_verbose_block(check: CommentsCheck, tmp_path: Path):
    # Arrange: a ten-line header opening with a copyright notice.
    header = "# Copyright 2024 Acme Corp. All rights reserved.\n"
    header += "".join(f"# Clause {i} of the permission notice.\n" for i in range(9))
    _write(root=tmp_path, name="licensed.py", body=f"{header}x = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert not any(v.rule == "CMT-002" for v in result.violations)


def test_cmt002_pep723_block_is_neither_verbose_nor_code(check: CommentsCheck, tmp_path: Path):
    # Arrange: a metadata block longer than the base allowance.
    block = "# /// script\n# dependencies = [\n"
    block += "".join(f'#     "dep{i}",\n' for i in range(8))
    block += "# ]\n# ///\n"
    _write(root=tmp_path, name="script.py", body=f"{block}x = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.violations == []


def test_cmt002_url_and_pragma_lines_are_not_measured(check: CommentsCheck, tmp_path: Path):
    # Arrange: a line long only because of its URL, a long pragma, and long prose.
    url = "# See https://example.com/" + "segment/" * 20
    pragma = "# pylint: disable=" + ",".join(f"rule-{i}" for i in range(20))
    prose = "# " + "word " * 30
    _write(root=tmp_path, name="long.py", body=f"{url}\n{pragma}\n{prose}\nx = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: only the prose is something the author can tighten.
    assert [v.line for v in result.violations if v.rule == "CMT-002"] == [3]


def test_cmt002_preamble_above_decorators_earns_the_function_allowance(
    check: CommentsCheck,
    tmp_path: Path,
):
    # Arrange: a ten-line preamble, two decorators, then a complex function.
    block = "".join(f"# explanation line {i}\n" for i in range(10))
    body = f"{block}@staticmethod\n@wraps(hard)\ndef hard(value):\n{_build_branchy_body(arms=12)}    return 0\n"
    _write(root=tmp_path, name="decorated.py", body=body)

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the decorators do not push the preamble out of reach.
    assert not any(v.rule == "CMT-002" for v in result.violations)


# --------------------------------------------------------------------------- #
# PROSE-003 on comments: emoji, not every symbol from the same blocks
# --------------------------------------------------------------------------- #


def test_prose003_on_comments_spares_typographic_symbols(tmp_path: Path):
    # Arrange: check marks, a star, a note, a joiner in Hindi, then real emoji.
    instance = CommentsCheck()
    instance.configure(settings={"emoji": True})
    body = (
        "# ✓ done ✗ failed ★ star ♪ note ☐ open\n"
        "# क्‍ष joined\n"
        "# \U0001f680 rocket\n"
        "# ✔ heavy check\n"
        "x = 1\n"
    )
    _write(root=tmp_path, name="symbols.py", body=body)

    # Act.
    result = instance.run(src_root=str(tmp_path))

    # Assert: the rocket and the heavy check mark are emoji; the rest is not.
    assert [v.line for v in result.violations if v.rule == "PROSE-003"] == [3, 4]
