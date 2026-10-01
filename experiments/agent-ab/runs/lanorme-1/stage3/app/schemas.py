"""HTTP request and response bodies, and the events of a streamed reply."""

from typing import Literal

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


class MessageOut(BaseModel):
    """One stored message of a conversation."""

    role: Literal["user", "assistant"]
    content: str


class SessionMessagesResponse(BaseModel):
    """A conversation's messages in order, as stored."""

    session_id: str
    messages: list[MessageOut]


class TokenEvent(BaseModel):
    """A streamed chunk of the reply."""

    type: Literal["token"] = "token"
    content: str


class DoneEvent(BaseModel):
    """The last streamed event: the turn is over and stored."""

    type: Literal["done"] = "done"
    session_id: str
    guardrails: list[GuardrailOut]
