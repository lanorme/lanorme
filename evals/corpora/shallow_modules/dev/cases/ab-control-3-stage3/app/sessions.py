"""Conversation memory: the stored turns of each tenant's sessions.

`SessionStore` is the persistence boundary, like `PolicyStore`: the app only
talks to that protocol, so the in-memory store can be swapped for a
database-backed one (set `app.state.session_store`).

Messages are stored as they were sent to the model and returned to the client,
i.e. already redacted when the tenant's policy redacts PII. A turn's user
message and reply are appended together once the turn has finished, so a
failed or blocked turn leaves nothing behind and a session's history is always
whole user/assistant pairs.
"""

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class SessionStore(Protocol):
    """Storage for conversations, keyed by (tenant, session)."""

    async def get(self, tenant_id: str, session_id: str) -> list[ChatMessage] | None:
        """The session's messages in order, or None if the session does not exist."""
        ...

    async def append(
        self, tenant_id: str, session_id: str, messages: Sequence[ChatMessage]
    ) -> None:
        """Append messages atomically, creating the session if needed."""
        ...

    async def delete(self, tenant_id: str, session_id: str) -> bool:
        """Forget the session. Returns False if it did not exist."""
        ...


class InMemorySessionStore:
    """Process-local `SessionStore`. Not shared between workers; lost on restart."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], list[ChatMessage]] = {}

    async def get(self, tenant_id: str, session_id: str) -> list[ChatMessage] | None:
        messages = self._sessions.get((tenant_id, session_id))
        return [m.model_copy() for m in messages] if messages is not None else None

    async def append(
        self, tenant_id: str, session_id: str, messages: Sequence[ChatMessage]
    ) -> None:
        stored = self._sessions.setdefault((tenant_id, session_id), [])
        stored.extend(m.model_copy() for m in messages)

    async def delete(self, tenant_id: str, session_id: str) -> bool:
        return self._sessions.pop((tenant_id, session_id), None) is not None
