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


# A cut just after this whitespace can never fall inside a match: emails hold no
# whitespace, and every space inside a phone candidate follows a digit or ")".
_SAFE_CUT = re.compile(r"(?<![\d)])\s")


class StreamingRedactor:
    """Redacts text that arrives in chunks, so PII split across chunks is still caught.

    Text is held back until a safe cut point, then redacted; the concatenated
    output equals redact_pii over the whole input. With enabled=False, chunks
    pass through unchanged.
    """

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled
        self._pending = ""
        self.changed = False

    def redact_chunk(self, chunk: str) -> str:
        """Take the next chunk and return whatever text is now safe to release."""
        if not self._enabled:
            return chunk
        self._pending += chunk
        cut = _find_last_safe_cut(self._pending)
        ready, self._pending = self._pending[:cut], self._pending[cut:]
        return self._redact(ready)

    def flush(self) -> str:
        """Release and redact everything still held back, at the end of the text."""
        ready, self._pending = self._pending, ""
        return self._redact(ready)

    def _redact(self, text: str) -> str:
        if not text:
            return ""
        redaction = redact_pii(text)
        self.changed = self.changed or redaction.changed
        return redaction.text


def _find_last_safe_cut(text: str) -> int:
    last = 0
    for match in _SAFE_CUT.finditer(text):
        last = match.end()
    return last
