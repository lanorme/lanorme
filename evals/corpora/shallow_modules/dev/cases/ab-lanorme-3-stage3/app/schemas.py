"""Request and response bodies of the HTTP contract."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.conversations import ConversationMessage, Role
from app.guardrails import GuardrailAction
from app.policies import GuardrailPolicy

# A topic must contain something to match; a blank one would silently block nothing.
type Topic = Annotated[str, StringConstraints(pattern=r"\S")]


class HealthResponse(BaseModel):
    """Liveness probe body."""

    status: Literal["ok"] = "ok"


class ChatRequest(BaseModel):
    """One user message within a session."""

    session_id: str
    message: str


class GuardrailEntry(BaseModel):
    """A guardrail that acted on the turn, such as ``pii_redaction`` / ``redacted``."""

    name: str
    action: str


    @classmethod
    def from_action(cls, action: GuardrailAction) -> Self:
        """Describe a domain guardrail action in the HTTP shape."""
        return cls(name=action.name, action=action.action)


class ChatResponse(BaseModel):
    """The agent's reply plus every guardrail that acted on the turn."""

    session_id: str
    reply: str
    guardrails: list[GuardrailEntry]


class TokenEvent(BaseModel):
    """One chunk of a streamed reply; the chunks join into the reply ``POST /chat`` returns."""

    type: Literal["token"] = "token"
    content: str


class DoneEvent(BaseModel):
    """The last event of a streamed reply, with every guardrail that acted on the turn."""

    type: Literal["done"] = "done"
    session_id: str
    guardrails: list[GuardrailEntry]


class MessageEntry(BaseModel):
    """One stored message of a conversation, redacted where the policy redacts."""

    role: Role
    content: str

    @classmethod
    def from_message(cls, message: ConversationMessage) -> Self:
        """Describe a stored message in the HTTP shape."""
        return cls(role=message.role, content=message.content)


class ConversationResponse(BaseModel):
    """A session's messages, oldest first."""

    session_id: str
    messages: list[MessageEntry]


class BlockedResponse(BaseModel):
    """Body of the 403 returned when a guardrail refuses a message."""

    error: Literal["blocked"] = "blocked"
    guardrail: str


class PolicyBody(BaseModel):
    """A tenant's full guardrail policy, as stored by PUT and returned by GET.

    Every field is required and unknown fields are refused, so a typo such as
    ``redact_PII`` fails with 422 instead of quietly keeping the default.
    Strict mode stops ``"5"`` or ``true`` passing as a tool-call limit.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    blocked_topics: list[Topic]
    redact_pii: bool
    max_tool_calls: Annotated[int, Field(ge=1)]
    system_prompt: str | None

    @classmethod
    def from_policy(cls, policy: GuardrailPolicy) -> Self:
        """Describe a domain policy in the HTTP shape."""
        return cls(
            blocked_topics=list(policy.blocked_topics),
            redact_pii=policy.redact_pii,
            max_tool_calls=policy.max_tool_calls,
            system_prompt=policy.system_prompt,
        )

    def to_policy(self) -> GuardrailPolicy:
        """Convert the validated body into the domain policy."""
        return GuardrailPolicy(
            blocked_topics=tuple(self.blocked_topics),
            redact_pii=self.redact_pii,
            max_tool_calls=self.max_tool_calls,
            system_prompt=self.system_prompt,
        )
