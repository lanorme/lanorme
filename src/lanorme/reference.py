"""The rule reference: ``lanorme rules`` and ``lanorme rule CODE``.

``rules`` lists what the registry declares. ``rule CODE`` prints the section of
``docs/RULES.md`` (bundled in the wheel) that documents one code, resolved by
the code itself: a heading naming the code exactly wins over one naming its
family (``NAMING-001..004``, ``SIZE-*``, ``JUNK-001/002``, ``TERM-NNN``), and a
family heading wins over nothing. Headings inside fenced code blocks are never
mistaken for section boundaries, so a TOML comment in an example cannot cut a
section short. Both commands have a JSON form for tooling.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files as resource_files
from pathlib import Path

from lanorme import extract_code, get_all_checks
from lanorme.errors import UsageError

_TOKEN_RE = re.compile(r"`([^`]+)`")
_CODE_RE = re.compile(r"^([A-Z]+)-(\d{3})$")
_RANGE_RE = re.compile(r"^([A-Z]+)-(\d{3})\.\.(\d{3})$")
_ALTERNATIVES_RE = re.compile(r"^([A-Z]+)-(\d{3}(?:/\d{3})+)$")


# --------------------------------------------------------------------------- #
# Registry view
# --------------------------------------------------------------------------- #


def _is_opt_in(check: object) -> bool:
    """True when the whole check ships off (``enabled = false`` by default)."""
    return hasattr(check, "enabled") and not getattr(check, "enabled")


def _is_opt_in_rule(*, check: object, code: str) -> bool:
    """True when *code* is off unless a setting enables it, or its check is opt-in."""
    return _is_opt_in(check) or code in getattr(check, "opt_in_rules", frozenset())


def _find_opt_in_setting(*, check: object, code: str) -> str | None:
    """The setting that turns *code* on: ``enabled`` for an opt-in check, else what it declares."""
    if _is_opt_in(check):
        return "enabled"
    if code in getattr(check, "opt_in_rules", frozenset()):
        setting = getattr(check, "opt_in_settings", {}).get(code)
        return setting if isinstance(setting, str) else None
    return None


def list_rules() -> list[dict[str, object]]:
    """Every registered check with its rules, as data."""
    listing: list[dict[str, object]] = []
    for check in sorted(get_all_checks().values(), key=lambda c: c.name):
        listing.append(
            {
                "check": check.name,
                "description": check.description,
                "opt_in": _is_opt_in(check),
                "rules": [
                    {
                        "code": extract_code(rule),
                        "rule": rule,
                        "opt_in": _is_opt_in_rule(check=check, code=extract_code(rule)),
                    }
                    for rule in check.rules
                ],
            },
        )
    return listing


def print_rules(*, as_json: bool = False) -> None:
    """Print every registered check and its rules."""
    listing = list_rules()
    if as_json:
        print(json.dumps(listing, indent=2))
        return
    if not listing:
        print("No checks registered.")
        return
    for entry in listing:
        opt_in = "  (opt-in)" if entry["opt_in"] else ""
        print(f"\n## {entry['check']} — {entry['description']}{opt_in}")
        for rule in entry["rules"]:
            print(f"  {rule['rule']}")


def _find_declaration(code: str) -> dict[str, object] | None:
    """The registry's view of *code*: its rule string, check, opt-in state and setting.

    ``opt_in`` is true when the check ships off or the rule itself does (the
    check's ``opt_in_rules``); ``opt_in_setting`` is the key that turns it on
    when the check names one, else ``None``.
    """
    for check in get_all_checks().values():
        for rule in check.rules:
            if extract_code(rule) == code:
                return {
                    "rule": rule,
                    "check": check.name,
                    "opt_in": _is_opt_in_rule(check=check, code=code),
                    "opt_in_setting": _find_opt_in_setting(check=check, code=code),
                }
    return None


# --------------------------------------------------------------------------- #
# Reference document
# --------------------------------------------------------------------------- #


def _locate_reference() -> Path | None:
    """Locate the rule reference Markdown, preferring the package-bundled copy."""
    bundled = resource_files("lanorme").joinpath("RULES.md")
    if bundled.is_file():
        return Path(str(bundled))
    for candidate in (
        Path(__file__).resolve().parents[2] / "docs" / "RULES.md",
        Path.cwd() / "docs" / "RULES.md",
    ):
        if candidate.is_file():
            return candidate
    return None


@dataclass(frozen=True)
class _Heading:
    index: int
    level: int
    text: str


def _collect_headings(lines: list[str]) -> list[_Heading]:
    """Markdown headings outside fenced code blocks."""
    found: list[_Heading] = []
    fence: str | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if fence is None and stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            continue
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            found.append(_Heading(index=index, level=level, text=line))
    return found


def _rate_token_match(*, token: str, code: str) -> int:
    """How specifically a backtick token names *code*: 2 exactly, 1 as a family, 0 not."""
    category, _dash, number = code.partition("-")
    if token == code:
        return 2
    if token in (f"{category}-*", f"{category}-NNN"):
        return 1
    if (match := _RANGE_RE.match(token)) and match.group(1) == category:
        return 1 if match.group(2) <= number <= match.group(3) else 0
    if (match := _ALTERNATIVES_RE.match(token)) and match.group(1) == category:
        return 2 if number in match.group(2).split("/") else 0
    return 0


def _find_section_bounds(
    *,
    headings: list[_Heading],
    code: str,
    total: int,
) -> tuple[int, int] | None:
    """The line range of the best heading for *code*: exact beats family, deeper beats shallower."""
    best: tuple[int, int, int] | None = None  # (specificity, level, position)
    for position, heading in enumerate(headings):
        specificity = max(
            (
                _rate_token_match(token=token, code=code)
                for token in _TOKEN_RE.findall(heading.text)
            ),
            default=0,
        )
        if specificity == 0:
            continue
        candidate = (specificity, heading.level, -position)
        if best is None or candidate > best:
            best = candidate
    if best is None:
        return None
    position = -best[2]
    start = headings[position]
    end = total
    for later in headings[position + 1 :]:
        if later.level <= start.level:
            end = later.index
            break
    return start.index, end


def find_rule_section(*, code: str) -> str | None:
    """The reference section documenting *code*, or ``None`` when there is none."""
    reference = _locate_reference()
    if reference is None:
        return None
    lines = reference.read_text(encoding="utf-8").splitlines()
    bounds = _find_section_bounds(headings=_collect_headings(lines), code=code, total=len(lines))
    if bounds is None:
        return None
    start, end = bounds
    return "\n".join(lines[start:end]).rstrip() + "\n"


def describe_rule(*, code: str) -> dict[str, object] | None:
    """Everything known about *code*: the declaration and the reference section."""
    wanted = code.upper()
    declared = _find_declaration(wanted)
    section = find_rule_section(code=wanted)
    if declared is None and section is None:
        return None
    detail: dict[str, object] = {"code": wanted}
    detail.update(
        declared or {"rule": None, "check": None, "opt_in": None, "opt_in_setting": None},
    )
    detail["section"] = section
    return detail


def _describe_opt_in(*, opt_in: bool, setting: str | None, check: str) -> str:
    """How the ``rule`` header words the rule's default state."""
    if not opt_in:
        return "on by default"
    if setting == "enabled":
        return "opt-in, enable it in config"
    if setting:
        return f"opt-in via {setting} = true in [tool.lanorme.{check}]"
    return "opt-in"


def print_rule_detail(*, code: str, as_json: bool = False) -> None:
    """Print the reference for *code*; an unknown code is a usage error."""
    detail = describe_rule(code=code)
    if detail is None:
        raise UsageError(
            f"no reference section found for {code!r}. Run 'lanorme rules' for the list "
            "of emitted codes, or browse docs/RULES.md directly.",
        )
    if as_json:
        print(json.dumps(detail, indent=2))
        return
    if detail["rule"] is not None:
        state = _describe_opt_in(
            opt_in=bool(detail["opt_in"]),
            setting=detail["opt_in_setting"],
            check=str(detail["check"]),
        )
        print(f"{detail['rule']}\n  check: {detail['check']} ({state})\n")
    if detail["section"] is not None:
        print(detail["section"], end="")
