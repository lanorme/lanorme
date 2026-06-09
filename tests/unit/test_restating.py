"""Tests for the restating check (CMT-005, experimental, opt-in).

CMT-005 is a precision-first "restating / redundant comment" detector. It flags
only the narrowest defensible redundancy: a short comment whose entire content
is subsumed by the single simple statement it sits directly above (or trails on
the same line). A false positive (flagging a valuable comment) is the cardinal
sin, so the suite is weighted toward precision traps.

The check is default-off; every test enables it via ``configure`` and drives the
check object directly (mirroring ``test_strong_types.py``), so none of the
``lanorme.toml`` plumbing is exercised here. Assertions key on ``v.rule ==
"CMT-005"`` and never on the (truncated, fragile) message string.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.restating import RestatingCheck


@pytest.fixture
def check() -> RestatingCheck:
    """An enabled restating check (it is default-off otherwise)."""
    instance = RestatingCheck()
    instance.configure(settings={"enabled": True})
    return instance


def _write(*, root: Path, name: str, body: str) -> None:
    """Write a Python source file *name* under *root*."""
    (root / name).write_text(body, encoding="utf-8")


def _codes(result) -> list[str]:
    """The rule codes of all violations on *result*."""
    return [v.rule for v in result.violations]


# --------------------------------------------------------------------------- #
# Default-off
# --------------------------------------------------------------------------- #


def test_disabled_by_default_passes_silently(tmp_path: Path):
    # Arrange: a clear restatement, but the check is NOT enabled.
    _write(root=tmp_path, name="x.py", body="counter = 0\n# increment counter\ncounter += 1\n")

    # Act: run the unconfigured (default-off) check.
    result = RestatingCheck().run(src_root=str(tmp_path))

    # Assert: experimental check stays silent until opted in.
    assert result.status == Status.PASS
    assert result.violations == []


# --------------------------------------------------------------------------- #
# True positives
# --------------------------------------------------------------------------- #


def test_verb_echo_increment_fires(check: RestatingCheck, tmp_path: Path):
    # Arrange: a comment that restates an augmented-assignment increment.
    _write(root=tmp_path, name="v.py", body="counter = 0\n# increment counter\ncounter += 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: verb table maps "increment" -> AugAssign(Add), "counter" covered.
    assert result.status == Status.FAIL
    assert "CMT-005" in _codes(result)


def test_return_echo_fires(check: RestatingCheck, tmp_path: Path):
    # Arrange: a comment that restates a return statement.
    _write(root=tmp_path, name="r.py", body="def f(result):\n    # return result\n    return result\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    assert "CMT-005" in _codes(result)


def test_call_echo_fires(check: RestatingCheck, tmp_path: Path):
    # Arrange: a comment that restates a normalize() call assignment.
    _write(root=tmp_path, name="c.py", body='url = "x"\n# normalize url\nurl = normalize(url)\n')

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    assert "CMT-005" in _codes(result)


def test_control_echo_without_stray_words_fires(check: RestatingCheck, tmp_path: Path):
    # Arrange: "loop users" (no connective) over a for-loop. The verb "loop"
    # maps to For/While and "users" stems to the loop's "user" target.
    _write(root=tmp_path, name="l.py", body="users = []\n# loop users\nfor user in users:\n    pass\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    assert "CMT-005" in _codes(result)


def test_blank_line_between_comment_and_code_still_fires(check: RestatingCheck, tmp_path: Path):
    # Arrange: a blank line separates the comment from the statement. Adjacency
    # is AST-based (next statement), not next physical line, so it must fire.
    _write(root=tmp_path, name="b.py", body="counter = 0\n# increment counter\n\ncounter += 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    assert "CMT-005" in _codes(result)


def test_trailing_comment_fires(check: RestatingCheck, tmp_path: Path):
    # Arrange: the restatement trails on the same line as the statement.
    _write(root=tmp_path, name="t.py", body="counter = 0\ncounter += 1  # increment counter\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    assert "CMT-005" in _codes(result)


# --------------------------------------------------------------------------- #
# True negatives / precision traps
# --------------------------------------------------------------------------- #


def test_why_explanation_is_not_flagged(check: RestatingCheck, tmp_path: Path):
    # Arrange: a comment that explains the *why* (highest-value comment kind).
    _write(root=tmp_path, name="w.py", body="counter = 0\n# +1 because the header row is excluded\ncounter += 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: a valuable explanatory comment must never fire.
    assert result.status == Status.PASS
    assert "CMT-005" not in _codes(result)


def test_caveat_over_call_is_not_flagged(check: RestatingCheck, tmp_path: Path):
    # Arrange: a WARNING caveat about an in-place mutation.
    _write(root=tmp_path, name="cv.py", body="def g(url):\n    # WARNING: mutates input in place\n    normalize(url)\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert "CMT-005" not in _codes(result)


def test_section_header_is_not_flagged(check: RestatingCheck, tmp_path: Path):
    # Arrange: a rule-of-dashes section header above a statement.
    _write(root=tmp_path, name="s.py", body="# --- request parsing ---\nx = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert "CMT-005" not in _codes(result)


def test_allowlist_tag_exempts_an_otherwise_firing_comment(check: RestatingCheck, tmp_path: Path):
    # Arrange: two files over the SAME 'counter += 1'. The control comment
    # '# counter' is fully covered and fires; prefixing the NOTE tag must
    # exempt it -- proving the allowlist (not coverage) does the work.
    _write(root=tmp_path, name="ctrl.py", body="counter = 0\n# counter\ncounter += 1\n")
    _write(root=tmp_path, name="tag.py", body="counter = 0\n# NOTE counter\ncounter += 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the control fires, the NOTE-tagged twin does not.
    flagged = {v.file for v in result.violations if v.rule == "CMT-005"}
    assert "ctrl.py" in flagged
    assert "tag.py" not in flagged


def test_allowlist_word_always_exempts(check: RestatingCheck, tmp_path: Path):
    # Arrange: the caveat word "always" is in the allowlist regex.
    _write(root=tmp_path, name="al.py", body="counter = 0\n# always increment counter\ncounter += 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert "CMT-005" not in _codes(result)


def test_string_literal_hash_is_not_a_comment(check: RestatingCheck, tmp_path: Path):
    # Arrange: '# increment x' lives inside a STRING literal, not a comment.
    _write(root=tmp_path, name="str.py", body='x = 0\ny = "# increment x"\nx += 1\n')

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: tokenize never yields a COMMENT here; the cardinal FP trap holds.
    assert result.status == Status.PASS
    assert "CMT-005" not in _codes(result)


def test_stem_asymmetry_id_does_not_match_identifier(check: RestatingCheck, tmp_path: Path):
    # Arrange: the old placeholder's defect -- substring 'id' in 'identifier'.
    _write(root=tmp_path, name="id.py", body="# id\nidentifier = 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: stem-equality, not substring, so this must not fire.
    assert result.status == Status.PASS
    assert "CMT-005" not in _codes(result)


def test_comment_over_def_is_not_flagged(check: RestatingCheck, tmp_path: Path):
    # Arrange: a comment directly above a def (an API/contract construct).
    _write(root=tmp_path, name="d.py", body="# return result\ndef return_result(result):\n    return result\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the statement-shape gate excludes def/class nodes.
    assert result.status == Status.PASS
    assert "CMT-005" not in _codes(result)


def test_comment_block_is_suppressed(check: RestatingCheck, tmp_path: Path):
    # Arrange: two adjacent standalone comments form a block (likely prose).
    _write(root=tmp_path, name="blk.py", body="# increment counter\n# increment counter\ncounter += 1\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: a comment with an adjacent standalone comment is skipped.
    assert result.status == Status.PASS
    assert "CMT-005" not in _codes(result)


def test_content_word_cap_silences_long_comments(check: RestatingCheck, tmp_path: Path):
    # Arrange: two files. The 4-word comment fires; the 5-word one exceeds the
    # cap and is silent, even though both are fully covered by the code.
    _write(root=tmp_path, name="cap_ok.py", body="def g(alpha, beta, gamma):\n    # assign alpha beta gamma\n    alpha = beta = gamma\n")
    _write(root=tmp_path, name="cap_over.py", body="def h(alpha, beta, gamma, delta):\n    # assign alpha beta gamma delta\n    alpha = beta = gamma = delta\n")

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: only the within-cap comment fires.
    flagged = {v.file for v in result.violations if v.rule == "CMT-005"}
    assert "cap_ok.py" in flagged
    assert "cap_over.py" not in flagged


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #


def test_syntax_error_file_skipped_others_still_checked(check: RestatingCheck, tmp_path: Path):
    # Arrange: an unparseable file beside a genuine restatement.
    _write(root=tmp_path, name="broken.py", body="def (:::\n")
    _write(root=tmp_path, name="good.py", body="counter = 0\n# increment counter\ncounter += 1\n")

    # Act: the run must complete, skipping the bad file.
    result = check.run(src_root=str(tmp_path))

    # Assert: the sibling violation is still reported.
    assert result.status == Status.FAIL
    flagged = {v.file for v in result.violations if v.rule == "CMT-005"}
    assert "good.py" in flagged
