"""Per-tenant guardrail policy: which guardrails a tenant's chat turns run under."""

from dataclasses import dataclass

DEFAULT_TENANT = "default"


@dataclass(frozen=True, slots=True)
class GuardrailPolicy:
    """The blocked topics, PII handling, tool-call limit and extra instructions for a tenant.

    Raises ValueError when max_tool_calls is negative.
    """

    blocked_topics: tuple[str, ...]
    redact_pii: bool
    max_tool_calls: int
    system_prompt: str | None = None

    def __post_init__(self) -> None:
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must not be negative")
