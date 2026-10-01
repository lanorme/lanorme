from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ADMIN
from tests.fakes import (
    ModelDownError,
    ScriptedChatModel,
    endless_calculator_calls,
    failing_after,
    script,
)

ClientFor = Callable[[ScriptedChatModel], TestClient]
NO_REDACTION = {
    "blocked_topics": ["crypto"],
    "redact_pii": False,
    "max_tool_calls": 5,
    "system_prompt": None,
}


def say(client: TestClient, message: str, *, session: str = "s-1", tenant: str = "acme") -> dict:
    response = client.post(
        "/chat", json={"session_id": session, "message": message}, headers={"X-Tenant-ID": tenant}
    )
    assert response.status_code == 200, response.text
    return response.json()


def history(client: TestClient, *, session: str = "s-1", tenant: str = "acme") -> list[dict]:
    response = client.get(f"/sessions/{session}/messages", headers={"X-Tenant-ID": tenant})
    assert response.status_code == 200, response.text
    assert response.json()["session_id"] == session
    return response.json()["messages"]


def seen_by_model(model: ScriptedChatModel, call: int) -> list[tuple[str, str]]:
    return [(m.type, m.text) for m in model.prompts[call] if m.type in {"human", "ai"}]


def test_the_model_sees_earlier_turns_of_the_session(client_for: ClientFor) -> None:
    # Given
    model = script("Noted.", "Banana.")
    client = client_for(model)

    # When
    say(client, "Remember the word banana")
    say(client, "What word?")

    # Then
    assert seen_by_model(model, 1) == [
        ("human", "Remember the word banana"),
        ("ai", "Noted."),
        ("human", "What word?"),
    ]


def test_messages_are_listed_in_order(client_for: ClientFor) -> None:
    # Given
    client = client_for(script("one", "two"))

    # When
    say(client, "first")
    say(client, "second")

    # Then
    assert history(client) == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "two"},
    ]


def test_the_same_session_id_under_two_tenants_is_two_conversations(
    client_for: ClientFor,
) -> None:
    # Given
    model = script("for acme", "for globex")
    client = client_for(model)

    # When
    say(client, "acme secret", tenant="acme")
    say(client, "globex question", tenant="globex")

    # Then
    assert seen_by_model(model, 1) == [("human", "globex question")]
    assert [m["content"] for m in history(client, tenant="globex")] == [
        "globex question",
        "for globex",
    ]


def test_missing_tenant_header_uses_the_default_tenants_sessions(client_for: ClientFor) -> None:
    # Given
    client = client_for(script("hi"))

    # When
    client.post("/chat", json={"session_id": "s-1", "message": "hello"})

    # Then
    assert client.get("/sessions/s-1/messages").status_code == 200
    assert history(client, tenant="default")[0]["content"] == "hello"


def test_stored_messages_and_history_are_redacted(client_for: ClientFor) -> None:
    # Given
    model = script("I will mail help@corp.io", "Done.")
    client = client_for(model)

    # When
    say(client, "I'm bob@example.org")
    say(client, "Thanks")

    # Then
    assert history(client)[:2] == [
        {"role": "user", "content": "I'm [REDACTED_EMAIL]"},
        {"role": "assistant", "content": "I will mail [REDACTED_EMAIL]"},
    ]
    assert "bob@example.org" not in str(model.prompts[1])
    assert "help@corp.io" not in str(model.prompts[1])


def test_history_is_redacted_again_when_a_tenant_turns_redaction_on(
    admin_client_for: ClientFor,
) -> None:
    # Given a turn stored while the tenant did not redact
    model = script("Call 555-123-4567", "ok")
    client = admin_client_for(model)
    client.put("/tenants/acme/policy", json=NO_REDACTION, headers=ADMIN)
    say(client, "I'm bob@example.org")

    # When the tenant switches redaction on and talks again
    client.put("/tenants/acme/policy", json=NO_REDACTION | {"redact_pii": True}, headers=ADMIN)
    say(client, "hello")

    # Then the model no longer sees the earlier PII, though it stays stored as it was
    assert seen_by_model(model, 1)[:2] == [
        ("human", "I'm [REDACTED_EMAIL]"),
        ("ai", "Call [REDACTED_PHONE]"),
    ]
    assert history(client)[0]["content"] == "I'm bob@example.org"


def test_a_blocked_message_is_not_stored(client_for: ClientFor) -> None:
    # Given
    model = script("ok", "fine")
    client = client_for(model)
    say(client, "hello")

    # When
    blocked = client.post(
        "/chat", json={"session_id": "s-1", "message": "weapons?"}, headers={"X-Tenant-ID": "acme"}
    )
    say(client, "and now?")

    # Then
    assert blocked.status_code == 403
    assert [m["content"] for m in history(client)] == ["hello", "ok", "and now?", "fine"]
    assert "weapons" not in str(model.prompts[1])


def test_a_blocked_first_message_creates_no_session(client_for: ClientFor) -> None:
    # Given
    client = client_for(script())

    # When
    client.post("/chat", json={"session_id": "new", "message": "malware"})

    # Then
    assert client.get("/sessions/new/messages").status_code == 404


def test_a_turn_stopped_by_the_tool_limit_is_stored_with_its_notice(
    client_for: ClientFor,
) -> None:
    # Given
    client = client_for(ScriptedChatModel(messages=endless_calculator_calls()))

    # When
    reply = say(client, "Keep adding")["reply"]

    # Then
    assert history(client) == [
        {"role": "user", "content": "Keep adding"},
        {"role": "assistant", "content": reply},
    ]


def test_a_turn_that_fails_is_not_stored(client_for: ClientFor) -> None:
    # Given a model that answers once and then fails
    client = client_for(failing_after("one"))
    say(client, "first", tenant="default")

    # When
    with pytest.raises(ModelDownError):
        client.post("/chat", json={"session_id": "s-1", "message": "second"})

    # Then
    assert [m["content"] for m in history(client, tenant="default")] == ["first", "one"]


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_an_unknown_session_is_404(client_for: ClientFor, method: str) -> None:
    # Given
    client = client_for(script("hi"))
    say(client, "hello", tenant="acme")

    # When
    url = "/sessions/s-1/messages" if method == "GET" else "/sessions/s-1"
    other_tenant = client.request(method, url, headers={"X-Tenant-ID": "globex"})
    other_session = client.request(method, url.replace("s-1", "s-2"), headers={"X-Tenant-ID": "acme"})

    # Then
    assert other_tenant.status_code == 404
    assert other_session.status_code == 404


def test_delete_forgets_the_conversation(client_for: ClientFor) -> None:
    # Given
    model = script("one", "two")
    client = client_for(model)
    say(client, "remember banana")

    # When
    deleted = client.delete("/sessions/s-1", headers={"X-Tenant-ID": "acme"})
    gone = client.get("/sessions/s-1/messages", headers={"X-Tenant-ID": "acme"})
    say(client, "what word?")

    # Then
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert gone.status_code == 404
    assert seen_by_model(model, 1) == [("human", "what word?")]


def test_delete_leaves_the_other_tenants_session_alone(client_for: ClientFor) -> None:
    # Given
    client = client_for(script("a", "b"))
    say(client, "hi", tenant="acme")
    say(client, "hi", tenant="globex")

    # When
    client.delete("/sessions/s-1", headers={"X-Tenant-ID": "acme"})

    # Then
    assert len(history(client, tenant="globex")) == 2
