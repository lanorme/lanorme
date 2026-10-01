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
_DIGITS_BEFORE = re.compile(r"[\w.]\Z")
_DIGITS_AFTER = re.compile(r"^(?:\w|\.\d)")
_MIN_DIGITS = 7
_MAX_DIGITS = 15
# Inside a _PHONE match a space always sits between two of these; emails hold
# no whitespace at all. StreamRedactor relies on both facts to split safely.
_PHONE_SPACE_NEIGHBOURS = frozenset("0123456789()")


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


class StreamRedactor:
    """Redacts text that arrives in pieces exactly as ``redact_pii`` redacts it whole.

    Text is held back until it reaches a split point that no email or phone
    number can span: whitespace other than a space, or a space next to a
    character that never flanks a space inside a phone number. The two sides
    of such a point redact the same apart as together, so the redacted pieces
    concatenate to the redaction of the whole text.
    """

    def __init__(self) -> None:
        self._pending = ""
        self.changed = False

    def feed(self, text: str) -> str:
        """Take the next piece; return whatever redacted text is now final."""
        self._pending += text
        split = _last_safe_split(self._pending)
        ready, self._pending = self._pending[:split], self._pending[split:]
        return self._redact(ready)

    def flush(self) -> str:
        """Return the redaction of everything still held back."""
        ready, self._pending = self._pending, ""
        return self._redact(ready)

    def _redact(self, text: str) -> str:
        """Redact ``text``, remembering whether anything was replaced."""
        redaction = redact_pii(text)
        self.changed = self.changed or redaction.changed
        return redaction.text


def _last_safe_split(text: str) -> int:
    """Return the last index ``text`` can be split at without cutting PII, else 0."""
    for index in range(len(text) - 1, 0, -1):
        if _is_safe_split(text=text, index=index):
            return index
    return 0


def _is_safe_split(*, text: str, index: int) -> bool:
    """Tell whether no email or phone number can contain ``text[index]``.

    A space needs the character after it, which may still be on its way.
    """
    char = text[index]
    if char != " ":
        return char.isspace()
    following = text[index + 1 : index + 2]
    return bool(following) and not {text[index - 1], following} <= _PHONE_SPACE_NEIGHBOURS
