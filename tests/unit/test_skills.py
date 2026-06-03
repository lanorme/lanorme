"""Tests for the SKILL-001..006 Agent Skill compliance check.

The check is deterministic, so these are precise behaviour tests: a fixture
corpus that locks the valid-vs-invalid verdicts, plus tmp_path cases for the
spec boundaries and the parser's never-fail-on-uncertainty contract.
"""

from __future__ import annotations

from pathlib import Path

from lanorme import Status
from lanorme.checks.skills import SkillsCheck

_CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "skills"


def _run(tmp_path: Path, *, dirname: str, content: str, files: dict[str, str] | None = None):
    """Write tmp_path/<dirname>/SKILL.md (plus any extra files) and run the check."""
    skill_dir = tmp_path / dirname
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    for rel, body in (files or {}).items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    result = SkillsCheck().run(src_root=str(tmp_path))
    return result


def _codes(result) -> set[str]:
    return {v.rule for v in result.violations} | {w.rule for w in result.warnings}


def _frontmatter(*, name: str, description: str = "A valid description.", extra: str = "") -> str:
    block = f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n# Body\n"
    return block


# --- Fixture corpus: locks valid-vs-invalid verdicts ------------------------ #


def test_valid_fixtures_are_clean():
    # Arrange + Act: scan each valid skill fixture in isolation.
    for case in ("valid-minimal", "valid-folded", "valid-links"):
        result = SkillsCheck().run(src_root=str(_CORPUS / case))
        # Assert: no findings at all.
        assert result.status == Status.PASS
        assert not result.violations and not result.warnings


def test_invalid_fixtures_fire_expected_rule():
    # Arrange: each fixture directory is labelled by the rule it should trip.
    expected = {
        "invalid-no-frontmatter": "SKILL-001",
        "bad-name-mismatch": "SKILL-001",
        "missing-desc": "SKILL-002",
        "bad-meta": "SKILL-003",
        "broken-link": "SKILL-005",
        "unterminated": "SKILL-006",
    }
    for case, code in expected.items():
        # Act
        result = SkillsCheck().run(src_root=str(_CORPUS / case))
        # Assert: the expected rule is present.
        assert code in _codes(result), f"{case} should fire {code}, got {_codes(result)}"


# --- name (SKILL-001) ------------------------------------------------------- #


def test_name_must_match_directory(tmp_path: Path):
    # Act
    result = _run(tmp_path, dirname="real-dir", content=_frontmatter(name="other-name"))
    # Assert
    assert "SKILL-001" in _codes(result)


def test_name_length_boundary(tmp_path: Path):
    # Arrange: a 64-char name is valid, 65 is not (directory matches in both).
    ok = "a" * 64
    bad = "a" * 65
    # Act + Assert
    assert "SKILL-001" not in _codes(_run(tmp_path, dirname=ok, content=_frontmatter(name=ok)))
    assert "SKILL-001" in _codes(_run(tmp_path, dirname=bad, content=_frontmatter(name=bad)))


def test_name_illegal_characters(tmp_path: Path):
    # Act: underscore is not allowed (directory matches, so this isolates the char rule).
    result = _run(tmp_path, dirname="bad_name", content=_frontmatter(name="bad_name"))
    # Assert
    assert "SKILL-001" in _codes(result)


def test_name_consecutive_hyphens(tmp_path: Path):
    # Act
    result = _run(tmp_path, dirname="a--b", content=_frontmatter(name="a--b"))
    # Assert
    assert "SKILL-001" in _codes(result)


def test_name_missing_is_failure(tmp_path: Path):
    # Act
    result = _run(tmp_path, dirname="d", content="---\ndescription: no name here.\n---\n")
    # Assert
    assert "SKILL-001" in _codes(result)


# --- description (SKILL-002) ------------------------------------------------ #


def test_description_empty_is_failure(tmp_path: Path):
    # Act
    result = _run(tmp_path, dirname="d", content="---\nname: d\ndescription:\n---\n")
    # Assert
    assert "SKILL-002" in _codes(result)


def test_description_too_long(tmp_path: Path):
    # Arrange: 1024 is allowed, 1025 is not.
    ok = "x" * 1024
    bad = "x" * 1025
    # Act + Assert
    assert "SKILL-002" not in _codes(_run(tmp_path, dirname="d", content=_frontmatter(name="d", description=ok)))
    assert "SKILL-002" in _codes(_run(tmp_path, dirname="d", content=_frontmatter(name="d", description=bad)))


def test_folded_description_is_valid(tmp_path: Path):
    # Arrange: a folded block scalar is real content, not an empty description.
    content = "---\nname: d\ndescription: >\n  A folded description that\n  spans two lines.\n---\n\n# Body\n"
    # Act
    result = _run(tmp_path, dirname="d", content=content)
    # Assert
    assert "SKILL-002" not in _codes(result)


# --- optional fields (SKILL-003) -------------------------------------------- #


def test_compatibility_too_long(tmp_path: Path):
    # Act
    extra = "compatibility: " + ("y" * 501) + "\n"
    result = _run(tmp_path, dirname="d", content=_frontmatter(name="d", extra=extra))
    # Assert
    assert "SKILL-003" in _codes(result)


def test_metadata_scalar_is_invalid(tmp_path: Path):
    # Act
    result = _run(tmp_path, dirname="d", content=_frontmatter(name="d", extra="metadata: not-a-map\n"))
    # Assert
    assert "SKILL-003" in _codes(result)


def test_metadata_map_is_valid(tmp_path: Path):
    # Arrange: a real nested map is fine.
    extra = "metadata:\n  author: me\n  version: \"1.0\"\n"
    # Act
    result = _run(tmp_path, dirname="d", content=_frontmatter(name="d", extra=extra))
    # Assert
    assert "SKILL-003" not in _codes(result)


# --- body size and links (SKILL-004 / SKILL-005) ---------------------------- #


def test_long_body_warns(tmp_path: Path):
    # Arrange: a body well over 500 lines.
    body = "\n".join(f"line {i}" for i in range(600))
    content = _frontmatter(name="d") + body
    # Act
    result = _run(tmp_path, dirname="d", content=content)
    # Assert: advisory warning, not a failure.
    assert "SKILL-004" in {w.rule for w in result.warnings}
    assert result.status != Status.FAIL


def test_broken_relative_link_warns(tmp_path: Path):
    # Act: link to a file that does not exist.
    content = _frontmatter(name="d") + "\nSee [ref](references/MISSING.md).\n"
    result = _run(tmp_path, dirname="d", content=content)
    # Assert
    assert "SKILL-005" in {w.rule for w in result.warnings}


def test_resolving_link_is_clean(tmp_path: Path):
    # Arrange: link target exists.
    content = _frontmatter(name="d") + "\nSee [ref](references/REF.md).\n"
    # Act
    result = _run(tmp_path, dirname="d", content=content, files={"references/REF.md": "# Ref\n"})
    # Assert
    assert "SKILL-005" not in _codes(result)


def test_external_and_anchor_links_ignored(tmp_path: Path):
    # Act
    body = "\n[site](https://example.com) and [top](#body) and [mail](mailto:x@y.z).\n"
    result = _run(tmp_path, dirname="d", content=_frontmatter(name="d") + body)
    # Assert
    assert "SKILL-005" not in _codes(result)


# --- parser never fails on uncertainty (SKILL-006) -------------------------- #


def test_block_scalar_name_warns_not_fails(tmp_path: Path):
    # Arrange: a name written as a block scalar is unreadable as a simple name.
    content = "---\nname: |\n  weird\ndescription: a description.\n---\n\n# Body\n"
    # Act
    result = _run(tmp_path, dirname="d", content=content)
    # Assert: SKILL-006 warns, and SKILL-001 does NOT fire on the unreadable value.
    assert "SKILL-006" in {w.rule for w in result.warnings}
    assert "SKILL-001" not in {v.rule for v in result.violations}


def test_inline_comment_on_name_is_stripped(tmp_path: Path):
    # Act: a trailing YAML comment must not become part of the name value.
    content = "---\nname: d # the skill name\ndescription: a description.\n---\n\n# Body\n"
    result = _run(tmp_path, dirname="d", content=content)
    # Assert: valid name, no false positive.
    assert not _codes(result)


def test_hash_without_space_is_kept(tmp_path: Path):
    # Act: 'd#x' has no space before '#', so it is part of the value, not a comment.
    result = _run(tmp_path, dirname="d", content=_frontmatter(name="d#x"))
    # Assert: still an illegal name.
    assert "SKILL-001" in _codes(result)


def test_flow_mapping_metadata_is_accepted(tmp_path: Path):
    # Act: a YAML flow mapping is a valid string map, not a bare scalar.
    extra = "metadata: {author: jane, team: core}\n"
    result = _run(tmp_path, dirname="d", content=_frontmatter(name="d", extra=extra))
    # Assert: no SKILL-003 false positive.
    assert "SKILL-003" not in _codes(result)


def test_separator_inside_block_scalar_does_not_close_frontmatter(tmp_path: Path):
    # Arrange: an indented '---' inside a literal description is content, not the fence.
    content = "---\nname: d\ndescription: |\n  A separator looks like:\n  ---\n  end.\n---\n\n# Body\n"
    # Act
    result = _run(tmp_path, dirname="d", content=content)
    # Assert: name is seen and description is non-empty.
    assert not _codes(result)


def test_link_in_indented_code_block_is_ignored(tmp_path: Path):
    # Act: a link inside a 4-space indented code block is sample text.
    content = _frontmatter(name="d") + "\n    [click](missing-in-indent.md)\n\nDone.\n"
    result = _run(tmp_path, dirname="d", content=content)
    # Assert
    assert "SKILL-005" not in _codes(result)


def test_only_skill_md_is_scanned(tmp_path: Path):
    # Arrange: a non-SKILL.md markdown file with broken frontmatter must be ignored.
    (tmp_path / "README.md").write_text("---\nnot: a skill\n", encoding="utf-8")
    # Act
    result = SkillsCheck().run(src_root=str(tmp_path))
    # Assert
    assert result.status == Status.PASS


def test_disabled_check_is_silent(tmp_path: Path):
    # Arrange
    check = SkillsCheck()
    check.configure(settings={"enabled": False})
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    # Act
    result = check.run(src_root=str(tmp_path))
    # Assert
    assert result.status == Status.PASS and not result.violations
