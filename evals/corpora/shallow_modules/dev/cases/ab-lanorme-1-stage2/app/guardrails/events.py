"""The record of a guardrail acting on a turn, as reported to the caller."""

from dataclasses import dataclass

PII_REDACTION = "pii_redaction"
TOPIC_BLOCKLIST = "topic_blocklist"
TOOL_CALL_LIMIT = "tool_call_limit"


@dataclass(frozen=True, slots=True)
class GuardrailEvent:
    """One guardrail that acted, and what it did."""

    name: str
    action: str


REDACTED = GuardrailEvent(name=PII_REDACTION, action="redacted")
BLOCKED = GuardrailEvent(name=TOPIC_BLOCKLIST, action="blocked")
STOPPED = GuardrailEvent(name=TOOL_CALL_LIMIT, action="stopped")
