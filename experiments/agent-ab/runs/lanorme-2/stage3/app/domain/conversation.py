"""The messages of one conversation, as stored after the tenant's guardrails."""

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """Who wrote a message in the conversation."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One stored message: the user's (redacted) text or the reply they were sent."""

    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class SessionKey:
    """A conversation is scoped to its tenant: one session ID under two tenants is two sessions."""

    tenant_id: str
    session_id: str
