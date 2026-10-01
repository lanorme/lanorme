"""Guardrail outcomes reported to the caller for each chat turn."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GuardrailAction:
    """A record that one guardrail acted on a turn, and how."""

    name: str
    action: str


PII_REDACTED = GuardrailAction(name="pii_redaction", action="redacted")
TOPIC_BLOCKED = GuardrailAction(name="topic_blocklist", action="blocked")
TOOL_CALLS_STOPPED = GuardrailAction(name="tool_call_limit", action="stopped")
