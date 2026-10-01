import pytest
from httpx import Response
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.main import create_app
from tests.conftest import ADMIN_HEADERS, ClientFactory
from tests.fakes import ScriptedChatModel, build_clock_calls, build_scripted_model

DEFAULT_POLICY = {
    "blocked_topics": ["weapons", "malware"],
    "redact_pii": True,
    "max_tool_calls": 5,
    "system_prompt": None,
}
CUSTOM_POLICY = {
    "blocked_topics": ["gambling"],
    "redact_pii": False,
    "max_tool_calls": 1,
    "system_prompt": "Always answer in French.",
}
ACME = "/tenants/acme/policy"


def post_chat(client: TestClient, message: str, *, tenant: str | None = None) -> Response:
    headers = {"X-Tenant-ID": tenant} if tenant is not None else {}
    return client.post("/chat", json={"session_id": "s", "message": message}, headers=headers)


def send_chat(client: TestClient, message: str, *, tenant: str | None = None) -> dict[str, object]:
    response = post_chat(client, message, tenant=tenant)
    assert response.status_code == 200, response.text
    return response.json()


def get_chat_status(client: TestClient, message: str, *, tenant: str | None = None) -> int:
    return post_chat(client, message, tenant=tenant).status_code


def store(client: TestClient, path: str, policy: dict[str, object]) -> None:
    response = client.put(path, json=policy, headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text


def read_human_text(model: ScriptedChatModel, call: int = 0) -> list[str]:
    return [msg.text for msg in model.calls[call] if msg.type == "human"]


def read_system_text(model: ScriptedChatModel, call: int = 0) -> str:
    return next(msg.text for msg in model.calls[call] if msg.type == "system")


# Policy endpoints


def test_unknown_tenant_gets_the_default_policy(make_admin_client: ClientFactory) -> None:
    # Given
    client = make_admin_client(build_scripted_model())

    # When
    response = client.get(ACME, headers=ADMIN_HEADERS)

    # Then
    assert response.status_code == 200
    assert response.json() == DEFAULT_POLICY


def test_put_stores_and_returns_the_policy(make_admin_client: ClientFactory) -> None:
    # Given
    client = make_admin_client(build_scripted_model())

    # When
    put = client.put(ACME, json=CUSTOM_POLICY, headers=ADMIN_HEADERS)
    got = client.get(ACME, headers=ADMIN_HEADERS)

    # Then
    assert put.status_code == 200
    assert put.json() == CUSTOM_POLICY
    assert got.json() == CUSTOM_POLICY
    assert client.get("/tenants/globex/policy", headers=ADMIN_HEADERS).json() == DEFAULT_POLICY


def test_put_replaces_an_earlier_policy(make_admin_client: ClientFactory) -> None:
    # Given
    client = make_admin_client(build_scripted_model())
    store(client, ACME, CUSTOM_POLICY)
    replacement = {**DEFAULT_POLICY, "blocked_topics": [], "max_tool_calls": 0}

    # When
    store(client, ACME, replacement)

    # Then
    assert client.get(ACME, headers=ADMIN_HEADERS).json() == replacement


def test_delete_falls_back_to_the_default(make_admin_client: ClientFactory) -> None:
    # Given
    client = make_admin_client(build_scripted_model())
    store(client, ACME, CUSTOM_POLICY)

    # When
    first = client.delete(ACME, headers=ADMIN_HEADERS)
    again = client.delete(ACME, headers=ADMIN_HEADERS)

    # Then
    assert first.status_code == 204
    assert first.content == b""
    assert again.status_code == 204
    assert client.get(ACME, headers=ADMIN_HEADERS).json() == DEFAULT_POLICY


def test_default_policy_follows_the_environment(
    make_admin_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("BLOCKED_TOPICS", "gambling")
    monkeypatch.setenv("MAX_TOOL_CALLS", "2")

    # When
    client = make_admin_client(build_scripted_model())

    # Then
    assert client.get(ACME, headers=ADMIN_HEADERS).json() == {
        **DEFAULT_POLICY,
        "blocked_topics": ["gambling"],
        "max_tool_calls": 2,
    }


@pytest.mark.parametrize(
    "body",
    [
        {key: value for key, value in CUSTOM_POLICY.items() if key != "redact_pii"},
        {key: value for key, value in CUSTOM_POLICY.items() if key != "system_prompt"},
        {**CUSTOM_POLICY, "blocked_topics": "gambling"},
        {**CUSTOM_POLICY, "blocked_topics": [1, 2]},
        {**CUSTOM_POLICY, "redact_pii": "yes"},
        {**CUSTOM_POLICY, "redact_pii": 1},
        {**CUSTOM_POLICY, "max_tool_calls": -1},
        {**CUSTOM_POLICY, "max_tool_calls": "3"},
        {**CUSTOM_POLICY, "max_tool_calls": 2.5},
        {**CUSTOM_POLICY, "system_prompt": 7},
        [],
        None,
    ],
)
def test_invalid_policy_is_422_and_not_stored(
    make_admin_client: ClientFactory, body: object
) -> None:
    # Given
    client = make_admin_client(build_scripted_model())

    # When
    response = client.put(ACME, json=body, headers=ADMIN_HEADERS)

    # Then
    assert response.status_code == 422
    assert client.get(ACME, headers=ADMIN_HEADERS).json() == DEFAULT_POLICY


def test_unparseable_policy_is_422(make_admin_client: ClientFactory) -> None:
    client = make_admin_client(build_scripted_model())

    response = client.put(
        ACME, content=b"{nope", headers={**ADMIN_HEADERS, "content-type": "application/json"}
    )

    assert response.status_code == 422


# Admin key


@pytest.mark.parametrize("headers", [{}, {"X-Admin-Key": "wrong"}, {"X-Admin-Key": ""}])
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_missing_or_wrong_admin_key_is_401(
    make_admin_client: ClientFactory, method: str, headers: dict[str, str]
) -> None:
    # Given
    client = make_admin_client(build_scripted_model())
    store(client, ACME, CUSTOM_POLICY)

    # When
    response = client.request(method, ACME, json=DEFAULT_POLICY, headers=headers)

    # Then
    assert response.status_code == 401
    assert client.get(ACME, headers=ADMIN_HEADERS).json() == CUSTOM_POLICY


def test_auth_is_checked_before_the_body(make_admin_client: ClientFactory) -> None:
    client = make_admin_client(build_scripted_model())

    response = client.put(ACME, json={"redact_pii": "no"})

    assert response.status_code == 401


@pytest.mark.parametrize("configured", [None, ""])
def test_without_a_configured_key_every_request_is_401(
    monkeypatch: pytest.MonkeyPatch, configured: str | None
) -> None:
    # Given
    if configured is not None:
        monkeypatch.setenv("ADMIN_API_KEY", configured)
    client = TestClient(create_app(build_scripted_model()))

    # When
    statuses = {client.get(ACME, headers=headers).status_code for headers in ({}, {"X-Admin-Key": ""})}

    # Then
    assert statuses == {401}


def test_admin_key_is_read_when_the_app_is_created(
    make_admin_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    client = make_admin_client(build_scripted_model())

    # When
    monkeypatch.setenv("ADMIN_API_KEY", "rotated-later")

    # Then
    assert client.get(ACME, headers=ADMIN_HEADERS).status_code == 200
    assert client.get(ACME, headers={"X-Admin-Key": "rotated-later"}).status_code == 401


def test_chat_needs_no_admin_key(make_admin_client: ClientFactory) -> None:
    client = make_admin_client(build_scripted_model(AIMessage(content="hi")))

    assert send_chat(client, "hello", tenant="acme")["reply"] == "hi"


# Chat under a tenant policy


def test_tenant_blocklist_replaces_the_default(make_admin_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(AIMessage(content="Malware is malicious software."))
    client = make_admin_client(model)
    store(client, ACME, CUSTOM_POLICY)

    # When
    blocked = post_chat(client, "GAMBLING odds", tenant="acme")
    allowed = send_chat(client, "what is malware?", tenant="acme")

    # Then
    assert blocked.status_code == 403
    assert blocked.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert allowed["reply"] == "Malware is malicious software."
    assert len(model.calls) == 1


def test_other_tenants_keep_the_default_blocklist(make_admin_client: ClientFactory) -> None:
    # Given
    client = make_admin_client(build_scripted_model(AIMessage(content="Odds vary.")))
    store(client, ACME, CUSTOM_POLICY)

    # When
    statuses = [
        get_chat_status(client, "malware", tenant="globex"),
        get_chat_status(client, "malware"),
        get_chat_status(client, "malware", tenant=""),
    ]

    # Then
    assert statuses == [403, 403, 403]
    assert send_chat(client, "gambling odds", tenant="globex")["reply"] == "Odds vary."


def test_without_redaction_pii_passes_through_both_ways(make_admin_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(AIMessage(content="I will call +44 20 7946 0958"))
    client = make_admin_client(model)
    store(client, ACME, CUSTOM_POLICY)

    # When
    body = send_chat(client, "I'm jane@example.com, 555-123-4567", tenant="acme")

    # Then
    assert read_human_text(model) == ["I'm jane@example.com, 555-123-4567"]
    assert body["reply"] == "I will call +44 20 7946 0958"
    assert body["guardrails"] == []


def test_redaction_on_for_a_tenant_still_redacts(make_admin_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(AIMessage(content="Noted: ops@corp.com"))
    client = make_admin_client(model)
    store(client, ACME, {**CUSTOM_POLICY, "redact_pii": True})

    # When
    body = send_chat(client, "mail me at jane@example.com", tenant="acme")

    # Then
    assert read_human_text(model) == ["mail me at [REDACTED_EMAIL]"]
    assert body["reply"] == "Noted: [REDACTED_EMAIL]"
    assert body["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]


def test_tenant_tool_limit_applies(make_admin_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(*build_clock_calls(2), AIMessage(content="never sent"))
    client = make_admin_client(model)
    store(client, ACME, CUSTOM_POLICY)

    # When
    body = send_chat(client, "time twice", tenant="acme")

    # Then
    assert "limit of 1 tool calls" in body["reply"]
    assert body["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]
    assert len(model.calls) == 2


def test_raised_tool_limit_allows_more_calls(make_admin_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(*build_clock_calls(7), AIMessage(content="Seven checks done."))
    client = make_admin_client(model)
    store(client, ACME, {**DEFAULT_POLICY, "max_tool_calls": 7})

    # When
    body = send_chat(client, "check the time seven times", tenant="acme")

    # Then
    assert body == {"session_id": "s", "reply": "Seven checks done.", "guardrails": []}


def test_tenant_system_prompt_is_added_to_the_instructions(
    make_admin_client: ClientFactory,
) -> None:
    # Given
    model = build_scripted_model(AIMessage(content="Bonjour."), AIMessage(content="Hello."))
    client = make_admin_client(model)
    store(client, ACME, CUSTOM_POLICY)

    # When
    send_chat(client, "hi", tenant="acme")
    send_chat(client, "hi", tenant="globex")

    # Then
    assert "Always answer in French." in read_system_text(model, call=0)
    assert "use the calculator tool" in read_system_text(model, call=0).lower()
    assert "Always answer in French." not in read_system_text(model, call=1)


def test_policy_changes_apply_to_the_next_turn(make_admin_client: ClientFactory) -> None:
    # Given
    client = make_admin_client(build_scripted_model(AIMessage(content="Weapons history.")))
    store(client, ACME, CUSTOM_POLICY)
    allowed = get_chat_status(client, "weapons history", tenant="acme")

    # When
    client.delete(ACME, headers=ADMIN_HEADERS)

    # Then
    assert allowed == 200
    assert get_chat_status(client, "weapons history", tenant="acme") == 403


def test_default_tenant_can_have_its_own_policy(make_admin_client: ClientFactory) -> None:
    # Given
    client = make_admin_client(build_scripted_model(AIMessage(content="Malware is bad.")))

    # When
    store(client, "/tenants/default/policy", {**DEFAULT_POLICY, "blocked_topics": []})

    # Then
    assert send_chat(client, "what is malware?")["reply"] == "Malware is bad."
    assert get_chat_status(client, "malware", tenant="acme") == 403
