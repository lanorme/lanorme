"""Conversation memory: the turns of each tenant's sessions, and where they are kept.

Storage sits behind ``ConversationStore`` so the in-memory version used today
can be swapped for a database without touching the callers. Messages are kept
exactly as the guardrails left them, so a redacting tenant never stores PII.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

type Role = Literal["user", "assistant"]

USER: Role = "user"
ASSISTANT: Role = "assistant"


class UnknownConversationError(LookupError):
    """No conversation is stored under the key."""


@dataclass(frozen=True, slots=True)
class ConversationKey:
    """Names one conversation. Sessions belong to a tenant, so two tenants never share one."""

    tenant_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """One stored message, as the model and the caller saw it."""

    role: Role
    content: str


class ConversationStore(Protocol):
    """Persistence for conversations; a conversation exists once a turn is stored in it."""

    async def list_messages(self, key: ConversationKey) -> tuple[ConversationMessage, ...] | None:
        """Return the conversation's messages in order, or ``None`` when it does not exist."""
        ...

    async def append_messages(self, *, key: ConversationKey, messages: Sequence[ConversationMessage]) -> None:
        """Add messages to the end of the conversation, creating it when needed.

        The messages of one call are stored together, so the two halves of a
        turn are never separated by another turn.
        """
        ...

    async def delete_conversation(self, key: ConversationKey) -> None:
        """Forget the conversation, or raise ``UnknownConversationError`` when there is none."""
        ...


class InMemoryConversationStore:
    """A ``ConversationStore`` held in a dict; conversations are lost when the process exits."""

    def __init__(self) -> None:
        self._conversations: dict[ConversationKey, list[ConversationMessage]] = {}

    async def list_messages(self, key: ConversationKey) -> tuple[ConversationMessage, ...] | None:
        """Return the conversation's messages in order, or ``None`` when it does not exist."""
        messages = self._conversations.get(key)
        return None if messages is None else tuple(messages)

    async def append_messages(self, *, key: ConversationKey, messages: Sequence[ConversationMessage]) -> None:
        """Add messages to the end of the conversation, creating it when needed."""
        self._conversations.setdefault(key, []).extend(messages)

    async def delete_conversation(self, key: ConversationKey) -> None:
        """Forget the conversation, or raise ``UnknownConversationError`` when there is none."""
        if self._conversations.pop(key, None) is None:
            raise UnknownConversationError(key)
