"""Guardrail primitives: PII redaction and topic blocklist."""

import re
from dataclasses import dataclass

EMAIL_PLACEHOLDER = "[REDACTED_EMAIL]"
PHONE_PLACEHOLDER = "[REDACTED_PHONE]"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# A phone number: optional +country code, then digit groups separated by
# spaces, dots or dashes, with optional parentheses around one group. We
# require 7-15 digits in total (E.164 max is 15) so that short numbers,
# years and prices are left alone.
_PHONE_CANDIDATE_RE = re.compile(
    r"(?<![\w+])"  # not glued to a preceding word, digit or plus
    r"(?:\+\d{1,3}[\s.-]?)?"
    r"(?:\(\d{1,4}\)[\s.-]?)?"
    r"\d{2,4}(?:[\s.-]?\d{2,4}){1,4}"
    r"(?!\w)"
)


@dataclass(frozen=True)
class GuardrailEvent:
    name: str
    action: str


PII_EVENT = GuardrailEvent("pii_redaction", "redacted")
BLOCKLIST_EVENT = GuardrailEvent("topic_blocklist", "blocked")
TOOL_LIMIT_EVENT = GuardrailEvent("tool_call_limit", "stopped")


_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _replace_phone(match: re.Match[str]) -> str:
    text = match.group(0)
    if _ISO_DATE_RE.fullmatch(text):
        return text
    digits = sum(c.isdigit() for c in text)
    if 7 <= digits <= 15:
        return PHONE_PLACEHOLDER
    return text


def redact_pii(text: str) -> tuple[str, bool]:
    """Return ``text`` with emails and phone numbers masked, and whether anything changed."""
    redacted = _EMAIL_RE.sub(EMAIL_PLACEHOLDER, text)
    redacted = _PHONE_CANDIDATE_RE.sub(_replace_phone, redacted)
    return redacted, redacted != text


class TopicBlocklist:
    """Case-insensitive whole-word matcher for blocked topics."""

    def __init__(self, topics: list[str]) -> None:
        self.topics = [t for t in (t.strip() for t in topics) if t]
        if self.topics:
            alternatives = "|".join(re.escape(t) for t in self.topics)
            # \b fails next to non-word characters, so use explicit lookarounds.
            self._pattern: re.Pattern[str] | None = re.compile(
                rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE
            )
        else:
            self._pattern = None

    def matches(self, text: str) -> bool:
        return bool(self._pattern and self._pattern.search(text))


# Every character an email or phone match can contain. A character outside this
# set can never be inside a match, and it satisfies the phone pattern's
# lookarounds (it is neither \w nor "+").
_MATCH_CHAR_RE = re.compile(r"[\w.%+\-@()\s]")
# Inside a phone number, whitespace only ever follows a digit or ")".
_BEFORE_PHONE_SPACE_RE = re.compile(r"[\d)]")


class StreamingRedactor:
    """Redacts PII in text that arrives in chunks, even when a match spans chunks.

    ``feed`` returns the redacted text that is safe to emit so far, holding back
    any tail that could still turn out to be part of an email or phone number;
    ``finish`` returns the rest. The concatenation of everything returned equals
    ``redact_pii`` applied to the concatenation of everything fed.
    """

    def __init__(self) -> None:
        self._pending = ""
        # The last character already emitted, to judge a cut at the buffer's start.
        self._last = ""
        self.redacted = False

    def feed(self, chunk: str) -> str:
        self._pending += chunk
        cut = self._safe_cut()
        if cut == 0:
            return ""
        head, self._pending = self._pending[:cut], self._pending[cut:]
        self._last = head[-1]
        return self._redact(head)

    def finish(self) -> str:
        head, self._pending = self._pending, ""
        return self._redact(head)

    def _redact(self, text: str) -> str:
        redacted, changed = redact_pii(text)
        self.redacted |= changed
        return redacted

    def _safe_cut(self) -> int:
        """The largest index such that no match can span it: the character just
        before it can belong to no email or phone number, whatever follows."""
        text = self._pending
        for i in range(len(text) - 1, -1, -1):
            char = text[i]
            if not _MATCH_CHAR_RE.match(char):
                return i + 1
            if char.isspace():
                before = text[i - 1] if i else self._last
                if not _BEFORE_PHONE_SPACE_RE.match(before):
                    return i + 1
        return 0
