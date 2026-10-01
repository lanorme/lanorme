import asyncio

import pytest

from app.conversations import (
    InMemoryConversationStore,
    Role,
    SessionKey,
    StoredMessage,
    UnknownSessionError,
)

ACME_S1 = SessionKey(tenant_id="acme", session_id="s-1")
GLOBEX_S1 = SessionKey(tenant_id="globex", session_id="s-1")
QUESTION = StoredMessage(role=Role.USER, content="hi")
ANSWER = StoredMessage(role=Role.ASSISTANT, content="hello")


def test_an_unknown_session_loads_as_none() -> None:
    assert asyncio.run(InMemoryConversationStore().load(ACME_S1)) is None


def test_appended_messages_load_in_order() -> None:
    # Given
    store = InMemoryConversationStore()

    # When
    asyncio.run(store.append(key=ACME_S1, messages=[QUESTION, ANSWER]))
    asyncio.run(store.append(key=ACME_S1, messages=[ANSWER, QUESTION]))

    # Then
    assert asyncio.run(store.load(ACME_S1)) == (QUESTION, ANSWER, ANSWER, QUESTION)


def test_sessions_are_scoped_to_their_tenant() -> None:
    # Given
    store = InMemoryConversationStore()

    # When
    asyncio.run(store.append(key=ACME_S1, messages=[QUESTION]))

    # Then
    assert asyncio.run(store.load(GLOBEX_S1)) is None


def test_delete_forgets_only_that_session_and_reports_unknown_ones() -> None:
    # Given
    store = InMemoryConversationStore()
    asyncio.run(store.append(key=ACME_S1, messages=[QUESTION]))
    asyncio.run(store.append(key=GLOBEX_S1, messages=[ANSWER]))

    # When
    asyncio.run(store.delete(ACME_S1))

    # Then
    with pytest.raises(UnknownSessionError):
        asyncio.run(store.delete(ACME_S1))
    assert asyncio.run(store.load(ACME_S1)) is None
    assert asyncio.run(store.load(GLOBEX_S1)) == (ANSWER,)


def test_loaded_history_is_a_snapshot() -> None:
    # Given
    store = InMemoryConversationStore()
    asyncio.run(store.append(key=ACME_S1, messages=[QUESTION]))
    before = asyncio.run(store.load(ACME_S1))

    # When
    asyncio.run(store.append(key=ACME_S1, messages=[ANSWER]))

    # Then
    assert before == (QUESTION,)
