"""Reading and forgetting a tenant's stored conversations."""

from app.application.ports.conversation_repository import ConversationRepository
from app.domain.conversation import ChatMessage, SessionKey
from app.domain.errors import SessionNotFoundError


class ConversationService:
    """Exposes stored conversations to their tenant; unknown sessions are an error."""

    def __init__(self, *, repository: ConversationRepository) -> None:
        self._repository = repository

    async def list_messages(self, key: SessionKey) -> tuple[ChatMessage, ...]:
        """Return the session's messages oldest first, as stored.

        Raises SessionNotFoundError when the tenant has no such session.
        """
        messages = await self._repository.list_messages(key)
        if messages is None:
            raise SessionNotFoundError(session_id=key.session_id)
        return messages

    async def forget_conversation(self, key: SessionKey) -> None:
        """Delete the session's messages.

        Raises SessionNotFoundError when the tenant has no such session.
        """
        if await self._repository.delete_conversation(key) == 0:
            raise SessionNotFoundError(session_id=key.session_id)
