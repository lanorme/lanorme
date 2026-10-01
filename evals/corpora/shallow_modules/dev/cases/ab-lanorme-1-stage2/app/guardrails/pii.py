"""Email and phone number redaction.

Phone patterns are deliberately structural rather than "any run of digits",
so dates, IP addresses and arithmetic survive redaction.
"""

import re
from dataclasses import dataclass

EMAIL_TOKEN = "[REDACTED_EMAIL]"
PHONE_TOKEN = "[REDACTED_PHONE]"

_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"
)

_SEP = r"[ .-]?"
_PHONE = re.compile(
    "|".join(
        (
            # International: +44 20 7946 0958, +1 (555) 123-4567, +33 1 23 45 67 89
            rf"\+\d{{1,3}}(?:{_SEP}\(?\d{{1,4}}\)?){{2,5}}",
            # North American: (555) 123-4567, 555-123-4567, 555.123.4567, 1-555-123-4567
            rf"(?:1{_SEP})?(?:\(\d{{3}}\) ?|\d{{3}}[ .-])\d{{3}}[ .-]\d{{4}}",
            # UK national: 020 7946 0958, 07700 900123
            r"\b0\d{2,4}[ -]?\d{3,4}[ -]?\d{3,4}",
        )
    )
)
_DIGITS_BEFORE = re.compile(r"[\w.]$")
_DIGITS_AFTER = re.compile(r"^(?:\w|\.\d)")
_MIN_DIGITS = 7
_MAX_DIGITS = 15


@dataclass(frozen=True, slots=True)
class Redaction:
    """Redacted text and whether anything in it was replaced."""

    text: str
    changed: bool


def redact_pii(text: str) -> Redaction:
    """Replace email addresses and phone numbers with placeholder tokens."""
    redacted = _EMAIL.sub(EMAIL_TOKEN, text)
    redacted = _PHONE.sub(lambda match: _phone_replacement(match=match), redacted)
    return Redaction(text=redacted, changed=redacted != text)


def _phone_replacement(*, match: re.Match[str]) -> str:
    """Keep a match that is glued to surrounding digits or too short to be a number."""
    candidate = match.group()
    before = match.string[: match.start()]
    after = match.string[match.end() :]
    glued = bool(_DIGITS_BEFORE.search(before) or _DIGITS_AFTER.match(after))
    digit_count = sum(char.isdigit() for char in candidate)
    if glued or not _MIN_DIGITS <= digit_count <= _MAX_DIGITS:
        return candidate
    return PHONE_TOKEN
