import asyncio

from app.domain.conversation import ChatMessage, Role, SessionKey
from app.infrastructure.repositories.in_memory_conversations import InMemoryConversationRepository

KEY = SessionKey(tenant_id="acme", session_id="s-1")
HELLO = ChatMessage(role=Role.USER, content="hello")
HI = ChatMessage(role=Role.ASSISTANT, content="hi")


def test_unknown_session_has_no_messages() -> None:
    assert asyncio.run(InMemoryConversationRepository().list_messages(KEY)) is None


def test_messages_are_appended_in_order() -> None:
    # Given
    repository = InMemoryConversationRepository()
    asyncio.run(repository.create_messages(key=KEY, messages=[HELLO, HI]))

    # When
    asyncio.run(repository.create_messages(key=KEY, messages=[HI]))

    # Then
    assert asyncio.run(repository.list_messages(KEY)) == (HELLO, HI, HI)


def test_appending_nothing_does_not_start_a_session() -> None:
    # Given
    repository = InMemoryConversationRepository()

    # When
    asyncio.run(repository.create_messages(key=KEY, messages=[]))

    # Then
    assert asyncio.run(repository.list_messages(KEY)) is None


def test_same_session_id_under_another_tenant_is_separate() -> None:
    # Given
    repository = InMemoryConversationRepository()
    asyncio.run(repository.create_messages(key=KEY, messages=[HELLO]))

    # When
    other = asyncio.run(repository.list_messages(SessionKey(tenant_id="other", session_id="s-1")))

    # Then
    assert other is None


def test_delete_forgets_and_counts_what_it_deleted() -> None:
    # Given
    repository = InMemoryConversationRepository()
    asyncio.run(repository.create_messages(key=KEY, messages=[HELLO, HI]))

    # When
    deleted = [asyncio.run(repository.delete_conversation(KEY)) for _ in range(2)]

    # Then
    assert deleted == [2, 0]
    assert asyncio.run(repository.list_messages(KEY)) is None


def test_listed_messages_are_a_snapshot() -> None:
    # Given
    repository = InMemoryConversationRepository()
    asyncio.run(repository.create_messages(key=KEY, messages=[HELLO]))
    snapshot = asyncio.run(repository.list_messages(KEY))

    # When
    asyncio.run(repository.create_messages(key=KEY, messages=[HI]))

    # Then
    assert snapshot == (HELLO,)
