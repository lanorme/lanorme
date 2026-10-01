"""Port for storing conversations, keyed by tenant and session."""

from collections.abc import Sequence
from typing import Protocol

from app.domain.conversation import ChatMessage, SessionKey


class ConversationRepository(Protocol):
    """Stored conversations; a session with no messages stored is simply absent."""

    async def list_messages(self, key: SessionKey) -> tuple[ChatMessage, ...] | None:
        """Return the session's messages oldest first, or None when it has none."""
        ...

    async def create_messages(self, *, key: SessionKey, messages: Sequence[ChatMessage]) -> None:
        """Append the messages to the session, starting the conversation when it is new."""
        ...

    async def delete_conversation(self, key: SessionKey) -> int:
        """Forget the session's messages; return how many were deleted (0 for an unknown session)."""
        ...
