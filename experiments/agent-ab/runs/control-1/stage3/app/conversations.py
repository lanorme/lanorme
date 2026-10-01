"""Conversation memory: the stored turns of each tenant's sessions."""

import threading
from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

Role = Literal["user", "assistant"]


class StoredMessage(BaseModel):
    """One message as stored: content is already redacted where the tenant's policy redacts."""

    model_config = ConfigDict(frozen=True)

    role: Role
    content: str


class ConversationStore(Protocol):
    """Storage for conversations keyed by (tenant, session); a database can implement this.

    Sessions are scoped to the tenant: the same ``session_id`` under two tenants is two
    separate conversations.
    """

    async def get(self, tenant_id: str, session_id: str) -> list[StoredMessage] | None:
        """The session's messages in order, or None if the session is unknown."""
        ...

    async def append(
        self, tenant_id: str, session_id: str, messages: Sequence[StoredMessage]
    ) -> None:
        """Atomically append ``messages``, creating the session if needed."""
        ...

    async def delete(self, tenant_id: str, session_id: str) -> bool:
        """Forget the session; return whether it existed."""
        ...


class InMemoryConversationStore:
    """Process-local store; conversations are lost on restart."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], list[StoredMessage]] = {}
        # A thread lock (never held across an await) works from any event loop.
        self._lock = threading.Lock()

    async def get(self, tenant_id: str, session_id: str) -> list[StoredMessage] | None:
        with self._lock:
            messages = self._sessions.get((tenant_id, session_id))
            return list(messages) if messages is not None else None

    async def append(
        self, tenant_id: str, session_id: str, messages: Sequence[StoredMessage]
    ) -> None:
        with self._lock:
            self._sessions.setdefault((tenant_id, session_id), []).extend(messages)

    async def delete(self, tenant_id: str, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop((tenant_id, session_id), None) is not None
