"""Redaction of email addresses and phone numbers from free text."""

import re
from dataclasses import dataclass

EMAIL_PLACEHOLDER = "[REDACTED_EMAIL]"
PHONE_PLACEHOLDER = "[REDACTED_PHONE]"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# A run of digit groups with optional country code and bracketed area code.
# Colons and slashes are excluded at both ends so times and URLs stay intact.
_PHONE_CANDIDATE = re.compile(
    r"""
    (?<![\w+:/])
    (?:\+\d{1,3}[ .-]?)?
    (?:\(\d{1,4}\)[ .-]?)?
    \d{1,4}(?:[ .-]?\d{2,4}){1,4}
    (?![\w:/])
    """,
    re.VERBOSE,
)
_IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")

# E.164 allows at most 15 digits; 10 is the shortest national number we accept,
# which keeps ISO dates (8 digits) and short reference numbers out.
_MIN_PHONE_DIGITS = 10
_MAX_PHONE_DIGITS = 15


@dataclass(frozen=True, slots=True)
class Redaction:
    """Text after redaction, and whether anything in it was replaced."""

    text: str
    changed: bool


def redact_pii(text: str) -> Redaction:
    """Replace emails and phone numbers with placeholders.

    Emails go first so digits inside an address are never read as a phone number.
    """
    without_emails, email_count = _EMAIL.subn(EMAIL_PLACEHOLDER, text)
    redacted = _PHONE_CANDIDATE.sub(_replace_phone, without_emails)
    return Redaction(text=redacted, changed=email_count > 0 or redacted != without_emails)


def _replace_phone(match: re.Match[str]) -> str:
    candidate = match.group(0)
    if _is_phone_number(candidate):
        return PHONE_PLACEHOLDER
    return candidate


def _is_phone_number(candidate: str) -> bool:
    if _IPV4.fullmatch(candidate):
        return False
    digit_count = sum(char.isdigit() for char in candidate)
    return _MIN_PHONE_DIGITS <= digit_count <= _MAX_PHONE_DIGITS
