import asyncio

import pytest

from app.application.services.conversations import ConversationService
from app.domain.conversation import ChatMessage, Role, SessionKey
from app.domain.errors import SessionNotFoundError
from app.infrastructure.repositories.in_memory_conversations import InMemoryConversationRepository

KEY = SessionKey(tenant_id="acme", session_id="s-1")
HELLO = ChatMessage(role=Role.USER, content="hello")


def build_service(*messages: ChatMessage) -> ConversationService:
    repository = InMemoryConversationRepository()
    asyncio.run(repository.create_messages(key=KEY, messages=messages))
    return ConversationService(repository=repository)


def test_lists_the_stored_messages() -> None:
    assert asyncio.run(build_service(HELLO).list_messages(KEY)) == (HELLO,)


def test_unknown_session_is_not_found() -> None:
    with pytest.raises(SessionNotFoundError) as caught:
        asyncio.run(build_service().list_messages(KEY))

    assert caught.value.session_id == "s-1"


def test_forgotten_session_is_gone() -> None:
    # Given
    service = build_service(HELLO)

    # When
    asyncio.run(service.forget_conversation(KEY))

    # Then
    with pytest.raises(SessionNotFoundError):
        asyncio.run(service.list_messages(KEY))


def test_forgetting_an_unknown_session_is_not_found() -> None:
    with pytest.raises(SessionNotFoundError):
        asyncio.run(build_service().forget_conversation(KEY))
