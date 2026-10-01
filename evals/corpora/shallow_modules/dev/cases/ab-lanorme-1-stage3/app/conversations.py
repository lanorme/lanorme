"""Conversation memory: the stored turns of each tenant's chat sessions.

``ConversationStore`` is the seam a database adapter would implement. A
session exists once its first turn is stored; until then it is unknown.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class UnknownSessionError(LookupError):
    """The tenant has no session by that id."""


class Role(StrEnum):
    """Who wrote a stored message."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class StoredMessage:
    """One message of a conversation, as the tenant's policy left it."""

    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class SessionKey:
    """A session id is only unique within its tenant, so both name a conversation."""

    tenant_id: str
    session_id: str


class ConversationStore(Protocol):
    """Persistence for conversations, keyed by tenant and session."""

    async def load(self, key: SessionKey) -> tuple[StoredMessage, ...] | None:
        """Return the session's messages in order, or ``None`` for an unknown session."""
        ...

    async def append(self, *, key: SessionKey, messages: Sequence[StoredMessage]) -> None:
        """Add ``messages`` to the end of the session, creating it if needed.

        The messages land together, so a concurrent turn on the same session
        cannot interleave between a question and its answer.
        """
        ...

    async def delete(self, key: SessionKey) -> None:
        """Forget the session, raising ``UnknownSessionError`` when there is none."""
        ...


class InMemoryConversationStore:
    """A ``ConversationStore`` held in process memory, lost on restart."""

    def __init__(self) -> None:
        self._sessions: dict[SessionKey, list[StoredMessage]] = {}

    async def load(self, key: SessionKey) -> tuple[StoredMessage, ...] | None:
        """Return the session's messages in order, or ``None`` for an unknown session."""
        messages = self._sessions.get(key)
        return None if messages is None else tuple(messages)

    async def append(self, *, key: SessionKey, messages: Sequence[StoredMessage]) -> None:
        """Add ``messages`` to the end of the session, creating it if needed."""
        self._sessions.setdefault(key, []).extend(messages)

    async def delete(self, key: SessionKey) -> None:
        """Forget the session, raising ``UnknownSessionError`` when there is none."""
        if self._sessions.pop(key, None) is None:
            raise UnknownSessionError(key.session_id)
