"""Request and response bodies of the HTTP contract."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from app.domain.policy import GuardrailPolicy


class ChatRequest(BaseModel):
    """One user message within a session."""

    session_id: str
    message: str


class GuardrailOut(BaseModel):
    """A guardrail that acted on the turn."""

    name: str
    action: str


class ChatResponse(BaseModel):
    """The agent's reply after guardrails, with the guardrails that acted."""

    session_id: str
    reply: str
    guardrails: list[GuardrailOut]


class HealthResponse(BaseModel):
    """Liveness status."""

    status: str


class ErrorResponse(BaseModel):
    """A refused or failed turn; guardrail is set when a guardrail refused it."""

    error: str
    guardrail: str | None = None


class PolicyBody(BaseModel):
    """A tenant's full guardrail policy, as stored and as returned.

    Strict, so "true" or 5.0 are rejected rather than coerced, and every field
    is required: a PUT replaces the whole policy.
    """

    model_config = ConfigDict(strict=True)

    blocked_topics: list[str]
    redact_pii: bool
    max_tool_calls: int = Field(ge=0)
    system_prompt: str | None

    @classmethod
    def from_policy(cls, policy: GuardrailPolicy) -> Self:
        """Describe a domain policy in the wire format."""
        return cls(
            blocked_topics=list(policy.blocked_topics),
            redact_pii=policy.redact_pii,
            max_tool_calls=policy.max_tool_calls,
            system_prompt=policy.system_prompt,
        )

    def to_policy(self) -> GuardrailPolicy:
        """Build the domain policy this body describes."""
        return GuardrailPolicy(
            blocked_topics=tuple(self.blocked_topics),
            redact_pii=self.redact_pii,
            max_tool_calls=self.max_tool_calls,
            system_prompt=self.system_prompt,
        )
