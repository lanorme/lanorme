"""ConversationRepository kept in process memory; conversations are lost on restart."""

from collections.abc import Sequence

from app.application.ports.conversation_repository import ConversationRepository
from app.domain.conversation import ChatMessage, SessionKey


class InMemoryConversationRepository(ConversationRepository):
    """A dict of session key to message list. Messages are immutable, so they are stored as given."""

    def __init__(self) -> None:
        self._conversations: dict[SessionKey, list[ChatMessage]] = {}

    async def list_messages(self, key: SessionKey) -> tuple[ChatMessage, ...] | None:
        """Return a snapshot of the session's messages, or None."""
        messages = self._conversations.get(key)
        return tuple(messages) if messages is not None else None

    async def create_messages(self, *, key: SessionKey, messages: Sequence[ChatMessage]) -> None:
        """Append the messages, starting the session's list when it is new."""
        if messages:
            self._conversations.setdefault(key, []).extend(messages)

    async def delete_conversation(self, key: SessionKey) -> int:
        """Drop the session's messages and return how many there were."""
        return len(self._conversations.pop(key, []))
