"""Request and response bodies of the HTTP contract."""

from pydantic import BaseModel


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
