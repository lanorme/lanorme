"""HTTP request and response bodies."""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """A user message within a session."""

    session_id: str
    message: str


class GuardrailOut(BaseModel):
    """A guardrail that acted on the turn."""

    name: str
    action: str


class ChatResponse(BaseModel):
    """The agent's reply plus every guardrail that acted on the turn."""

    session_id: str
    reply: str
    guardrails: list[GuardrailOut]


class HealthResponse(BaseModel):
    """Liveness probe body."""

    status: str


class BlockedResponse(BaseModel):
    """Body returned with 403 when a guardrail refuses the message."""

    error: str
    guardrail: str
