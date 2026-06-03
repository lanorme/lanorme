"""SKILL-001..006: Agent Skill (``SKILL.md``) compliance.

Validates ``SKILL.md`` files against the Agent Skills specification
(https://agentskills.io/specification) plus a little house style:

    SKILL-001  name: required; 1-64 characters, lowercase ``a-z`` / ``0-9`` and
               hyphens only, no leading, trailing or consecutive hyphen, and it
               must match the parent directory name.
    SKILL-002  description: required; non-empty and at most 1024 characters.
    SKILL-003  optional fields well formed: compatibility at most 500 characters,
               metadata a string-to-string map, allowed-tools a string.
    SKILL-004  the SKILL.md body stays under 500 lines (progressive disclosure).  [warning]
    SKILL-005  relative Markdown links resolve to a file that exists.             [warning]
    SKILL-006  frontmatter is present but could not be parsed with confidence.    [warning]

SKILL-001 to SKILL-003 are build failing; SKILL-004 to SKILL-006 are advisory
warnings. The check is on by default: it only fires on files named ``SKILL.md``,
so it stays silent on a project that has no skills.

To keep precision high the parser never turns uncertainty into a failure. If the
frontmatter cannot be read cleanly, SKILL-006 is emitted and the unreadable
fields are left unvalidated rather than reported as missing.

Run:
    lanorme check . --check=skills
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.discovery import iter_files

NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500
BODY_MAX_LINES = 500

# A valid name: lowercase alphanumeric segments joined by single hyphens. This
# one pattern already forbids uppercase, underscores, leading or trailing
# hyphens, and consecutive hyphens; the individual messages below explain which.
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NAME_CHARS_RE = re.compile(r"^[a-z0-9-]+$")

# Block scalar indicator that may follow a key, with optional explicit-indent
# digit and chomping suffix: ``description: >``, ``: |``, ``: |2-``, ``: >+``.
_BLOCK_RE = re.compile(r"^[|>][0-9]*[+-]?$")

# Markdown link: [text](target). Reference-style and bare URLs are ignored.
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# Vendored or generated directories never scanned.
_SKIP_PARTS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"})


@dataclass(frozen=True)
class _Field:
    """One parsed top-level frontmatter key."""

    kind: str  # "scalar" | "block" | "map" | "flow" | "empty"
    value: str = ""
    pairs: dict[str, str] = field(default_factory=dict)


def _strip_bom(text: str) -> str:
    return text[1:] if text.startswith("﻿") else text


def _is_block_indicator(rest: str) -> bool:
    """True if a value is a YAML block scalar header (``|``, ``>``, ``|2-`` ...)."""
    return bool(_BLOCK_RE.match(rest))


def _unquote(value: str) -> str:
    """Strip one layer of matching surrounding quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _strip_comment(value: str) -> str:
    """Drop a YAML line comment from an unquoted scalar.

    A ``#`` begins a comment when it starts the value or follows whitespace, so
    ``foo # note`` becomes ``foo`` but ``foo#bar`` (no preceding space) is kept.
    """
    out: list[str] = []
    for index, char in enumerate(value):
        if char == "#" and (index == 0 or value[index - 1] in " \t"):
            break
        out.append(char)
    return "".join(out).strip()


def _scalar_field(rest: str) -> _Field:
    """Classify an inline value as a quoted/plain scalar or a flow collection."""
    if rest[:1] in "{[":
        return _Field(kind="flow", value=rest)  # flow map/list: accept, do not over-read
    if rest[:1] in "\"'":
        return _Field(kind="scalar", value=_unquote(rest))
    return _Field(kind="scalar", value=_strip_comment(rest))


def _extract_frontmatter(text: str) -> tuple[list[str] | None, str]:
    """Return (frontmatter_lines, status).

    status is "ok" (fenced block found), "none" (no opening fence), or
    "unterminated" (opening fence with no closing fence).
    """
    lines = _strip_bom(text).splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "none"
    # The closing fence is a document marker at column 0. Use rstrip (not strip)
    # so an indented '---' inside a '|' or '>' block scalar does not end the block.
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            return lines[1:index], "ok"
    return None, "unterminated"


def _collect_block(*, fm_lines: list[str], start: int) -> tuple[list[str], int]:
    """Collect the indented lines that belong to the key opened at *start*."""
    block: list[str] = []
    index = start
    while index < len(fm_lines):
        line = fm_lines[index]
        if line.strip() and line[:1] not in (" ", "\t"):
            break
        if line.strip():
            block.append(line)
        index += 1
    return block, index


def _parse_block(*, rest: str, block: list[str]) -> _Field:
    """Classify a key whose inline value was empty but has an indented block."""
    if _is_block_indicator(rest):
        joined = " ".join(line.strip() for line in block)
        return _Field(kind="block", value=joined)
    pairs: dict[str, str] = {}
    for line in block:
        match = re.match(r"^\s+([A-Za-z][\w-]*)\s*:\s*(.*)$", line)
        if match is None:
            return _Field(kind="block", value=" ".join(b.strip() for b in block))
        pairs[match.group(1)] = _unquote(match.group(2).strip())
    return _Field(kind="map", pairs=pairs)


def _parse_top_level(fm_lines: list[str]) -> dict[str, _Field]:
    """Parse top-level ``key: value`` entries, folding indented blocks and maps."""
    fields: dict[str, _Field] = {}
    index = 0
    while index < len(fm_lines):
        line = fm_lines[index]
        if not line.strip() or line[:1] in (" ", "\t") or line.lstrip().startswith("#"):
            index += 1
            continue
        match = re.match(r"^([A-Za-z][\w-]*)\s*:(.*)$", line)
        if match is None:
            index += 1
            continue
        key, rest = match.group(1), match.group(2).strip()
        if rest and not _is_block_indicator(rest):
            fields[key] = _scalar_field(rest)
            index += 1
            continue
        block, index = _collect_block(fm_lines=fm_lines, start=index + 1)
        fields[key] = _parse_block(rest=rest, block=block) if block else _Field(kind="empty")
    return fields


def _v(*, file: str, rule: str, message: str, fix: str) -> Violation:
    """Build a Violation at line 1 (frontmatter findings are file-level)."""
    return Violation(file=file, line=1, rule=rule, message=message, fix=fix)


def _name_findings(*, name: _Field | None, parent_dir: str, file: str) -> list[Violation]:
    """Validate the required ``name`` field against the spec and the directory."""
    if name is None:
        return [_v(
            file=file,
            rule="SKILL-001",
            message="Required frontmatter key 'name' is missing",
            fix="Add 'name: <skill-name>' matching the skill's directory name",
        )]
    if name.kind == "empty":
        return [_v(
            file=file,
            rule="SKILL-001",
            message="Frontmatter key 'name' is present but empty",
            fix="Set name to the skill's directory name",
        )]
    if name.kind != "scalar":
        return []  # block or map: unreadable as a name, SKILL-006 covers it
    value = name.value
    problems: list[tuple[str, str]] = []
    if len(value) > NAME_MAX:
        problems.append((
            f"name '{value}' is {len(value)} characters (max {NAME_MAX})",
            "Shorten the name to 64 characters or fewer",
        ))
    if not _NAME_CHARS_RE.match(value):
        problems.append((
            f"name '{value}' has characters other than lowercase letters, digits and hyphens",
            "Use only a-z, 0-9 and hyphens",
        ))
    elif value.startswith("-") or value.endswith("-"):
        problems.append((
            f"name '{value}' starts or ends with a hyphen",
            "Remove the leading or trailing hyphen",
        ))
    elif "--" in value:
        problems.append((
            f"name '{value}' contains consecutive hyphens",
            "Use single hyphens between segments",
        ))
    if _NAME_RE.match(value) and value != parent_dir:
        problems.append((
            f"name '{value}' does not match its directory '{parent_dir}'",
            f"Rename the directory to '{value}' or set name to '{parent_dir}'",
        ))
    return [_v(file=file, rule="SKILL-001", message=message, fix=fix) for message, fix in problems]


def _description_findings(*, description: _Field | None, file: str) -> list[Violation]:
    """Validate the required ``description`` field."""
    if description is None:
        return [_v(
            file=file,
            rule="SKILL-002",
            message="Required frontmatter key 'description' is missing",
            fix="Add a 'description' that says what the skill does and when to use it",
        )]
    if description.kind == "map":
        return []  # unreadable as text: SKILL-006 covers it
    text = description.value.strip()
    if not text:
        return [_v(
            file=file,
            rule="SKILL-002",
            message="Frontmatter key 'description' is empty",
            fix="Describe what the skill does and when to use it",
        )]
    if len(text) > DESCRIPTION_MAX:
        return [_v(
            file=file,
            rule="SKILL-002",
            message=f"description is {len(text)} characters (max {DESCRIPTION_MAX})",
            fix="Shorten the description to 1024 characters or fewer",
        )]
    return []


def _optional_findings(*, fields: dict[str, _Field], file: str) -> list[Violation]:
    """Validate the optional fields that have spec constraints."""
    found: list[Violation] = []
    compatibility = fields.get("compatibility")
    if compatibility is not None and compatibility.kind in ("scalar", "block"):
        if len(compatibility.value.strip()) > COMPATIBILITY_MAX:
            found.append(_v(
                file=file,
                rule="SKILL-003",
                message=f"compatibility is over {COMPATIBILITY_MAX} characters",
                fix="Shorten compatibility, or move detail into the body",
            ))
    metadata = fields.get("metadata")
    if metadata is not None and metadata.kind == "scalar" and metadata.value:
        found.append(_v(
            file=file,
            rule="SKILL-003",
            message="metadata must be a map of string keys to string values, not a single value",
            fix="Write metadata as indented 'key: value' pairs",
        ))
    tools = fields.get("allowed-tools")
    if tools is not None and tools.kind == "map":
        found.append(_v(
            file=file,
            rule="SKILL-003",
            message="allowed-tools must be a space-separated string, not a list or map",
            fix="Write allowed-tools as a single string, e.g. 'Bash(git:*) Read'",
        ))
    return found


def _uncertain_findings(*, fields: dict[str, _Field], file: str) -> list[Violation]:
    """Warn (never fail) when a required field parsed into an unreadable shape."""
    found: list[Violation] = []
    name = fields.get("name")
    if name is not None and name.kind in ("block", "map", "flow"):
        found.append(_v(
            file=file,
            rule="SKILL-006",
            message="Frontmatter key 'name' could not be read as a simple value",
            fix="Write name as a plain 'name: value' line so it can be validated",
        ))
    description = fields.get("description")
    if description is not None and description.kind == "map":
        found.append(_v(
            file=file,
            rule="SKILL-006",
            message="Frontmatter key 'description' could not be read as text",
            fix="Write description as a plain or folded value",
        ))
    return found


def _body_findings(*, body_lines: int, file: str) -> list[Violation]:
    if body_lines <= BODY_MAX_LINES:
        return []
    return [Violation(
        file=file,
        line=1,
        rule="SKILL-004",
        message=f"SKILL.md body is {body_lines} lines (keep under {BODY_MAX_LINES})",
        fix="Move detailed reference material into references/ files loaded on demand",
    )]


def _is_local_link(target: str) -> bool:
    """True if a Markdown link target is a relative path to a bundled file."""
    target = target.strip().split()[0] if target.strip() else ""
    target = target.strip("<>")
    if not target or target.startswith(("#", "/")):
        return False
    return not re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE)


def _link_findings(*, body: list[str], body_offset: int, skill_dir: Path, file: str) -> list[Violation]:
    """Flag relative Markdown links whose target file does not exist."""
    found: list[Violation] = []
    fence: str | None = None
    for offset, raw in enumerate(body):
        stripped = raw.lstrip()
        marker = stripped[:3]
        if fence is None and marker in ("```", "~~~"):
            fence = marker  # open a fenced block; only the same marker closes it
            continue
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if raw[:4] == "    " or raw[:1] == "\t":
            continue  # an indented code block is sample text, not a real link
        for match in _MD_LINK_RE.finditer(raw):
            target = match.group(1)
            if not _is_local_link(target):
                continue
            path = target.strip().split()[0].strip("<>").split("#", 1)[0]
            if not (skill_dir / path).exists():
                found.append(Violation(
                    file=file,
                    line=body_offset + offset,
                    rule="SKILL-005",
                    message=f"Markdown link target '{path}' does not exist",
                    fix="Fix the path, or add the referenced file",
                ))
    return found


@dataclass
class SkillsCheck:
    """Agent Skill (SKILL.md) compliance against the Agent Skills specification."""

    name: str = "skills"
    description: str = "Agent Skill (SKILL.md) compliance: frontmatter, naming, links"
    enabled: bool = True
    check_links: bool = True
    rules: list[str] = field(
        default_factory=lambda: [
            "SKILL-001: name present, well formed, and matching the directory",
            "SKILL-002: description present and within 1024 characters",
            "SKILL-003: optional fields (compatibility, metadata, allowed-tools) well formed",
            "SKILL-004: SKILL.md body kept under 500 lines",
            "SKILL-005: relative Markdown links resolve",
            "SKILL-006: frontmatter parses cleanly",
        ]
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.skills]`` configuration."""
        if "enabled" in settings:
            self.enabled = bool(settings["enabled"])
        if "check_links" in settings:
            self.check_links = bool(settings["check_links"])

    def _scan_file(self, *, path: Path, file: str) -> tuple[list[Violation], list[Violation]]:
        """Return (violations, warnings) for one SKILL.md file."""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return [], []

        fm_lines, status = _extract_frontmatter(text)
        if status == "none":
            missing = Violation(
                file=file,
                line=1,
                rule="SKILL-001",
                message="SKILL.md has no YAML frontmatter (required: name, description)",
                fix="Add a '---' delimited frontmatter block with name and description",
            )
            return [missing], []
        if status == "unterminated" or fm_lines is None:
            warn = Violation(
                file=file,
                line=1,
                rule="SKILL-006",
                message="Frontmatter opening '---' has no closing '---'",
                fix="Close the frontmatter block with a '---' line",
            )
            return [], [warn]

        fields = _parse_top_level(fm_lines)
        violations: list[Violation] = []
        violations += _name_findings(name=fields.get("name"), parent_dir=path.parent.name, file=file)
        violations += _description_findings(description=fields.get("description"), file=file)
        violations += _optional_findings(fields=fields, file=file)

        warnings = _uncertain_findings(fields=fields, file=file)
        warnings += self._warnings_for(text=text, fm_lines=fm_lines, path=path, file=file)
        return violations, warnings

    def _warnings_for(
        self, *, text: str, fm_lines: list[str], path: Path, file: str
    ) -> list[Violation]:
        """Advisory warnings: body size and relative-link resolution."""
        all_lines = _strip_bom(text).splitlines()
        body_offset = len(fm_lines) + 3  # opening fence, closing fence, 1-based
        body = all_lines[body_offset - 1 :]
        warnings = _body_findings(body_lines=len(body), file=file)
        if self.check_links:
            warnings += _link_findings(
                body=body, body_offset=body_offset, skill_dir=path.parent, file=file
            )
        return warnings

    def run(self, *, src_root: str) -> CheckResult:
        if not self.enabled:
            return CheckResult(check=self.name, status=Status.PASS)

        violations: list[Violation] = []
        warnings: list[Violation] = []
        root = Path(src_root)

        for path in iter_files(root):
            if path.name != "SKILL.md" or not path.is_file():
                continue
            if any(part in _SKIP_PARTS for part in path.parts):
                continue
            relative = str(path.relative_to(root))
            file_violations, file_warnings = self._scan_file(path=path, file=relative)
            violations.extend(file_violations)
            warnings.extend(file_warnings)

        status = Status.FAIL if violations else (Status.WARN if warnings else Status.PASS)
        return CheckResult(check=self.name, status=status, violations=violations, warnings=warnings)


register(SkillsCheck())
