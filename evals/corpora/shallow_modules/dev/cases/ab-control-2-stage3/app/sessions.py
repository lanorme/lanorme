"""Conversation memory: stored turns per (tenant, session) and where they are kept."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict


class StoredMessage(BaseModel):
    """One message of a conversation, as stored (already redacted where the policy redacts)."""

    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str


class ConversationStore(Protocol):
    """Storage for conversations. Async so a database-backed store can drop in.

    Conversations are keyed by tenant and session, so the same session id under
    two tenants is two separate conversations.
    """

    async def get(self, tenant_id: str, session_id: str) -> list[StoredMessage] | None:
        """The session's messages in order, or None if the session is unknown."""
        ...

    async def append(self, tenant_id: str, session_id: str, messages: Sequence[StoredMessage]) -> None:
        """Append `messages` atomically, creating the session if needed."""
        ...

    async def delete(self, tenant_id: str, session_id: str) -> bool:
        """Forget the session. Returns whether it existed."""
        ...


class InMemoryConversationStore:
    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], list[StoredMessage]] = {}

    async def get(self, tenant_id: str, session_id: str) -> list[StoredMessage] | None:
        messages = self._sessions.get((tenant_id, session_id))
        return None if messages is None else list(messages)

    async def append(self, tenant_id: str, session_id: str, messages: Sequence[StoredMessage]) -> None:
        self._sessions.setdefault((tenant_id, session_id), []).extend(messages)

    async def delete(self, tenant_id: str, session_id: str) -> bool:
        return self._sessions.pop((tenant_id, session_id), None) is not None
