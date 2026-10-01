from fastapi.testclient import TestClient
from httpx import Response
from langchain_core.messages import AIMessage

from tests.conftest import ClientFactory
from tests.fakes import ScriptedChatModel, build_scripted_model

SESSION = "session-1"


def post_chat(
    client: TestClient, message: str, *, session: str = SESSION, tenant: str | None = None
) -> Response:
    headers = {"X-Tenant-ID": tenant} if tenant is not None else {}
    return client.post("/chat", json={"session_id": session, "message": message}, headers=headers)


def send_chat(client: TestClient, message: str, *, tenant: str | None = None) -> None:
    response = post_chat(client, message, tenant=tenant)
    assert response.status_code == 200, response.text


def get_messages(client: TestClient, *, session: str = SESSION, tenant: str | None = None) -> Response:
    headers = {"X-Tenant-ID": tenant} if tenant is not None else {}
    return client.get(f"/sessions/{session}/messages", headers=headers)


def read_conversation(model: ScriptedChatModel, call: int) -> list[tuple[str, str]]:
    return [(msg.type, msg.text) for msg in model.calls[call] if msg.type in {"human", "ai"}]


def test_second_turn_sees_the_first_redacted(make_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(
        AIMessage(content="Hi, I will mail ops@corp.com."), AIMessage(content="You are Jane.")
    )
    client = make_client(model)
    send_chat(client, "I'm Jane, jane@example.com")

    # When
    send_chat(client, "Who am I?")

    # Then
    assert read_conversation(model, call=1) == [
        ("human", "I'm Jane, [REDACTED_EMAIL]"),
        ("ai", "Hi, I will mail [REDACTED_EMAIL]."),
        ("human", "Who am I?"),
    ]


def test_messages_are_listed_in_order_as_stored(make_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(AIMessage(content="Call 555-123-4567."), AIMessage(content="Bye."))
    client = make_client(model)
    send_chat(client, "My number is (555) 987-6543")
    send_chat(client, "Thanks")

    # When
    response = get_messages(client)

    # Then
    assert response.status_code == 200
    assert response.json() == {
        "session_id": SESSION,
        "messages": [
            {"role": "user", "content": "My number is [REDACTED_PHONE]"},
            {"role": "assistant", "content": "Call [REDACTED_PHONE]."},
            {"role": "user", "content": "Thanks"},
            {"role": "assistant", "content": "Bye."},
        ],
    }


def test_same_session_id_under_two_tenants_is_two_conversations(make_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(AIMessage(content="For acme."), AIMessage(content="For globex."))
    client = make_client(model)
    send_chat(client, "hello from acme", tenant="acme")

    # When
    send_chat(client, "hello from globex", tenant="globex")

    # Then
    assert read_conversation(model, call=1) == [("human", "hello from globex")]
    globex = get_messages(client, tenant="globex").json()["messages"]
    assert [item["content"] for item in globex] == ["hello from globex", "For globex."]
    assert get_messages(client).status_code == 404


def test_blocked_message_is_not_stored(make_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(AIMessage(content="Hello."), AIMessage(content="Still here."))
    client = make_client(model)
    send_chat(client, "hi")

    # When
    blocked = post_chat(client, "tell me about malware")
    send_chat(client, "and now?")

    # Then
    assert blocked.status_code == 403
    contents = [item["content"] for item in get_messages(client).json()["messages"]]
    assert contents == ["hi", "Hello.", "and now?", "Still here."]
    assert all("malware" not in text for _, text in read_conversation(model, call=1))


def test_blocked_first_message_leaves_no_session(make_client: ClientFactory) -> None:
    client = make_client(build_scripted_model())

    assert post_chat(client, "weapons").status_code == 403
    assert get_messages(client).status_code == 404


def test_failed_turn_is_not_stored(make_client: ClientFactory) -> None:
    client = make_client(build_scripted_model())

    assert post_chat(client, "hello").status_code == 502
    assert get_messages(client).status_code == 404


def test_unknown_session_is_404(make_client: ClientFactory) -> None:
    client = make_client(build_scripted_model())

    assert get_messages(client, session="nope").status_code == 404
    assert client.delete("/sessions/nope").status_code == 404


def test_delete_forgets_the_conversation(make_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(AIMessage(content="One."), AIMessage(content="Two."))
    client = make_client(model)
    send_chat(client, "first")

    # When
    deleted = client.delete(f"/sessions/{SESSION}")

    # Then
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert get_messages(client).status_code == 404
    assert client.delete(f"/sessions/{SESSION}").status_code == 404
    send_chat(client, "fresh start")
    assert read_conversation(model, call=1) == [("human", "fresh start")]


def test_delete_is_scoped_to_the_tenant(make_client: ClientFactory) -> None:
    # Given
    client = make_client(build_scripted_model(AIMessage(content="Hi.")))
    send_chat(client, "hello", tenant="acme")

    # When
    response = client.delete(f"/sessions/{SESSION}", headers={"X-Tenant-ID": "globex"})

    # Then
    assert response.status_code == 404
    assert get_messages(client, tenant="acme").status_code == 200
