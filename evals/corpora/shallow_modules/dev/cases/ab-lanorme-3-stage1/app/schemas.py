"""Request and response bodies of the HTTP contract."""

from typing import Literal

from pydantic import BaseModel


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


class ChatResponse(BaseModel):
    """The agent's reply plus every guardrail that acted on the turn."""

    session_id: str
    reply: str
    guardrails: list[GuardrailEntry]


class BlockedResponse(BaseModel):
    """Body of the 403 returned when a guardrail refuses a message."""

    error: Literal["blocked"] = "blocked"
    guardrail: str
