"""The inline suppression directives: ``# noqa`` and ``# lanorme: ignore[...]``.

Two readers share these: the run's inline-ignore stage, which drops a finding
whose line carries a covering directive, and the ``suppressions`` check, which
counts the directives against a budget. The patterns live here so both read
the same syntax.
"""

from __future__ import annotations

import re

from lanorme import extract_code
from lanorme.selectors import extract_category, is_code_matched

NOQA_RE = re.compile(r"#\s*noqa(?:\s*:\s*([A-Za-z0-9_,\-\s]+))?", re.IGNORECASE)
# A LaNorme-native directive ruff and other linters never read, so a project
# running both can silence a finding without ruff reporting an invalid `noqa`
# (ruff's parser cannot tokenise the hyphen in our codes, e.g. ``TYPE-001``).
IGNORE_RE = re.compile(
    r"#\s*lanorme\s*:\s*ignore(?:\s*\[([A-Za-z0-9_,\-\s]+)\])?",
    re.IGNORECASE,
)

# Categories no inline directive may silence. A budget on suppressions that an
# offender can waive on the offending line is not a budget, so the SUPPRESS
# family is answerable only in config, where the decision is reviewable.
UNSUPPRESSABLE = frozenset({"SUPPRESS"})


def _is_silenced_by_directive(*, pattern: re.Pattern[str], line: str, rule: str) -> bool:
    """True if a *pattern* directive on *line* covers *rule*.

    A directive with no code list (bare ``# noqa`` or ``# lanorme: ignore``)
    silences every rule on the line; a coded one silences only matching codes
    (exact code, category, or ``ALL``).
    """
    match = pattern.search(line)
    if match is None:
        return False
    if match.group(1) is None:
        return True
    codes = [c.strip() for c in match.group(1).split(",") if c.strip()]
    return is_code_matched(code=extract_code(rule), patterns=codes)


def is_silenced_inline(*, line: str, rule: str) -> bool:
    """True if a ``# noqa`` or ``# lanorme: ignore`` on *line* covers *rule*."""
    if extract_category(extract_code(rule)) in UNSUPPRESSABLE:
        return False
    return _is_silenced_by_directive(
        pattern=NOQA_RE,
        line=line,
        rule=rule,
    ) or _is_silenced_by_directive(
        pattern=IGNORE_RE,
        line=line,
        rule=rule,
    )
