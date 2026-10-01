"""Guardrails applied around each chat turn: PII redaction and a topic blocklist.

The tool-call limit is enforced inside the agent loop (see ``app.agent``); its
name and action live here so every guardrail's public vocabulary is in one place.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

PII_REDACTION = "pii_redaction"
TOPIC_BLOCKLIST = "topic_blocklist"
TOOL_CALL_LIMIT = "tool_call_limit"

REDACTED = "redacted"
BLOCKED = "blocked"
STOPPED = "stopped"

EMAIL_PLACEHOLDER = "[REDACTED_EMAIL]"
PHONE_PLACEHOLDER = "[REDACTED_PHONE]"

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# Candidate phone shapes: optional +country code, optional (area) group, then
# digit groups split by spaces, dots or hyphens. Candidates are then filtered
# by digit count so dates such as 2024-01-15 are left alone.
PHONE_PATTERN = re.compile(
    r"(?<![\w+])"
    r"(?:\+\d{1,3}[ .-]?)?"
    r"(?:\(\d{1,4}\)[ .-]?)?"
    r"\d{1,4}(?:[ .-]\d{2,4}){1,5}"
    r"(?![\w-])"
)
MIN_PHONE_DIGITS = 10
MIN_INTERNATIONAL_PHONE_DIGITS = 8
MAX_PHONE_DIGITS = 15


@dataclass(frozen=True, slots=True)
class GuardrailAction:
    """One guardrail that acted on a turn, as reported to the caller."""

    name: str
    action: str


@dataclass(frozen=True, slots=True)
class Redaction:
    """Text after PII redaction, and whether anything was replaced."""

    text: str
    changed: bool


def redact_pii(text: str) -> Redaction:
    """Replace email addresses and phone numbers with fixed placeholders.

    Emails go first so the digits inside an address are never read as a phone
    number.
    """
    without_emails = EMAIL_PATTERN.sub(EMAIL_PLACEHOLDER, text)
    redacted = PHONE_PATTERN.sub(replace_phone_match, without_emails)
    return Redaction(text=redacted, changed=redacted != text)


def replace_phone_match(match: re.Match[str]) -> str:
    """Return the placeholder when a candidate has a plausible digit count."""
    candidate = match.group()
    return PHONE_PLACEHOLDER if is_phone_number(candidate) else candidate


def is_phone_number(candidate: str) -> bool:
    """Judge a regex candidate by digit count (E.164 allows at most 15).

    A leading ``+`` marks an international number, which may be as short as
    8 digits; national formats need at least 10.
    """
    digits = sum(char.isdigit() for char in candidate)
    minimum = MIN_INTERNATIONAL_PHONE_DIGITS if candidate.startswith("+") else MIN_PHONE_DIGITS
    return minimum <= digits <= MAX_PHONE_DIGITS


class TopicBlocklist:
    """Case-insensitive, whole-word matcher for blocked topics.

    A topic may be several words (``"credit card fraud"``); any run of
    whitespace in the message matches the space between them.
    """

    def __init__(self, topics: Iterable[str]) -> None:
        alternatives = [r"\s+".join(map(re.escape, topic.split())) for topic in topics if topic.strip()]
        self._pattern = (
            re.compile(rf"(?<!\w)(?:{'|'.join(alternatives)})(?!\w)", re.IGNORECASE) if alternatives else None
        )

    def is_blocked(self, message: str) -> bool:
        """Say whether the message mentions any blocked topic."""
        return self._pattern is not None and self._pattern.search(message) is not None
