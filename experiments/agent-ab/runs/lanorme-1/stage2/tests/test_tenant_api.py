from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ADMIN
from tests.fakes import ScriptedChatModel, calling, endless_calculator_calls, script, tool_call

ClientFor = Callable[[ScriptedChatModel], TestClient]
DEFAULT_POLICY = {
    "blocked_topics": ["weapons", "malware"],
    "redact_pii": True,
    "max_tool_calls": 5,
    "system_prompt": None,
}
ACME_POLICY = {
    "blocked_topics": ["crypto"],
    "redact_pii": False,
    "max_tool_calls": 1,
    "system_prompt": "Always answer as Acme's support desk.",
}
POLICY_URL = "/tenants/acme/policy"
STOPPED = {"name": "tool_call_limit", "action": "stopped"}


def configured(client: TestClient, policy: dict, tenant: str = "acme") -> TestClient:
    response = client.put(f"/tenants/{tenant}/policy", json=policy, headers=ADMIN)
    assert response.status_code == 200, response.text
    return client


def chat_as(client: TestClient, tenant: str | None, message: str) -> dict:
    headers = {} if tenant is None else {"X-Tenant-ID": tenant}
    response = client.post(
        "/chat", json={"session_id": "s-1", "message": message}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_unknown_tenant_gets_the_default_policy(admin_client_for: ClientFor) -> None:
    response = admin_client_for(script()).get(POLICY_URL, headers=ADMIN)

    assert response.status_code == 200
    assert response.json() == DEFAULT_POLICY


def test_put_stores_and_returns_the_policy(admin_client_for: ClientFor) -> None:
    # Given
    client = admin_client_for(script())

    # When
    put = client.put(POLICY_URL, json=ACME_POLICY, headers=ADMIN)
    got = client.get(POLICY_URL, headers=ADMIN)

    # Then
    assert put.status_code == 200
    assert put.json() == ACME_POLICY
    assert got.json() == ACME_POLICY


def test_put_replaces_a_previous_policy(admin_client_for: ClientFor) -> None:
    # Given
    client = configured(admin_client_for(script()), ACME_POLICY)

    # When
    client.put(POLICY_URL, json=DEFAULT_POLICY | {"max_tool_calls": 9}, headers=ADMIN)

    # Then
    assert client.get(POLICY_URL, headers=ADMIN).json()["max_tool_calls"] == 9


def test_delete_falls_back_to_the_default(admin_client_for: ClientFor) -> None:
    # Given
    client = configured(admin_client_for(script()), ACME_POLICY)

    # When
    deleted = client.delete(POLICY_URL, headers=ADMIN)

    # Then
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(POLICY_URL, headers=ADMIN).json() == DEFAULT_POLICY


def test_delete_without_a_stored_policy_is_204(admin_client_for: ClientFor) -> None:
    response = admin_client_for(script()).delete(POLICY_URL, headers=ADMIN)

    assert response.status_code == 204


def test_policies_are_per_tenant(admin_client_for: ClientFor) -> None:
    client = configured(admin_client_for(script()), ACME_POLICY)

    assert client.get("/tenants/globex/policy", headers=ADMIN).json() == DEFAULT_POLICY


@pytest.mark.parametrize(
    "body",
    [
        {k: v for k, v in ACME_POLICY.items() if k != "redact_pii"},
        {k: v for k, v in ACME_POLICY.items() if k != "system_prompt"},
        ACME_POLICY | {"blocked_topics": "crypto"},
        ACME_POLICY | {"blocked_topics": [1]},
        ACME_POLICY | {"redact_pii": "false"},
        ACME_POLICY | {"max_tool_calls": -1},
        ACME_POLICY | {"max_tool_calls": "3"},
        ACME_POLICY | {"max_tool_calls": True},
        ACME_POLICY | {"system_prompt": 7},
        ACME_POLICY | {"surprise": 1},
        [],
    ],
)
def test_invalid_policy_body_is_422_and_not_stored(
    admin_client_for: ClientFor, body: dict | list
) -> None:
    # Given
    client = admin_client_for(script())

    # When
    response = client.put(POLICY_URL, json=body, headers=ADMIN)

    # Then
    assert response.status_code == 422
    assert client.get(POLICY_URL, headers=ADMIN).json() == DEFAULT_POLICY


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
@pytest.mark.parametrize("headers", [{}, {"X-Admin-Key": "wrong"}, {"X-Admin-Key": ""}])
def test_admin_endpoints_need_the_admin_key(
    admin_client_for: ClientFor, method: str, headers: dict
) -> None:
    # Given
    client = admin_client_for(script())

    # When
    response = client.request(method, POLICY_URL, json=ACME_POLICY, headers=headers)

    # Then
    assert response.status_code == 401
    assert client.get(POLICY_URL, headers=ADMIN).json() == DEFAULT_POLICY


def test_wrong_key_is_401_even_with_an_invalid_body(admin_client_for: ClientFor) -> None:
    response = admin_client_for(script()).put(
        POLICY_URL, json={"bad": 1}, headers={"X-Admin-Key": "wrong"}
    )

    assert response.status_code == 401


def test_without_admin_api_key_configured_every_key_is_refused(client_for: ClientFor) -> None:
    response = client_for(script()).get(POLICY_URL, headers={"X-Admin-Key": ""})

    assert response.status_code == 401


def test_admin_key_is_read_when_the_app_is_created(
    admin_client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    client = admin_client_for(script())

    # When
    monkeypatch.setenv("ADMIN_API_KEY", "rotated")

    # Then
    assert client.get(POLICY_URL, headers=ADMIN).status_code == 200
    assert client.get(POLICY_URL, headers={"X-Admin-Key": "rotated"}).status_code == 401


def test_tenant_blocked_topics_replace_the_default_ones(admin_client_for: ClientFor) -> None:
    # Given
    model = script("about weapons")
    client = configured(admin_client_for(model), ACME_POLICY)

    # When
    blocked = client.post(
        "/chat", json={"session_id": "s", "message": "crypto tips"}, headers={"X-Tenant-ID": "acme"}
    )
    allowed = chat_as(client, "acme", "weapons history")

    # Then
    assert blocked.status_code == 403
    assert blocked.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert allowed["reply"] == "about weapons"
    assert model.calls == 1


def test_other_tenants_keep_the_default_blocklist(admin_client_for: ClientFor) -> None:
    # Given
    model = script("crypto is fine here")
    client = configured(admin_client_for(model), ACME_POLICY)

    # When
    blocked = client.post(
        "/chat", json={"session_id": "s", "message": "weapons"}, headers={"X-Tenant-ID": "globex"}
    )
    allowed = chat_as(client, None, "crypto tips")

    # Then
    assert blocked.status_code == 403
    assert allowed["reply"] == "crypto is fine here"


def test_missing_tenant_header_means_the_default_tenant(admin_client_for: ClientFor) -> None:
    # Given
    client = configured(admin_client_for(script("never")), ACME_POLICY, tenant="default")

    # When
    response = client.post("/chat", json={"session_id": "s", "message": "crypto"})

    # Then
    assert response.status_code == 403


def test_redaction_off_passes_pii_through_both_ways(admin_client_for: ClientFor) -> None:
    # Given
    model = script("Call 555-123-4567 or mail help@corp.io")
    client = configured(admin_client_for(model), ACME_POLICY)

    # When
    body = chat_as(client, "acme", "I'm bob@example.org, +44 20 7946 0958")

    # Then
    assert model.prompts[0][-1].text == "I'm bob@example.org, +44 20 7946 0958"
    assert body["reply"] == "Call 555-123-4567 or mail help@corp.io"
    assert body["guardrails"] == []


def test_redaction_on_still_applies_to_a_custom_policy(admin_client_for: ClientFor) -> None:
    # Given
    model = script("Mail help@corp.io")
    client = configured(admin_client_for(model), ACME_POLICY | {"redact_pii": True})

    # When
    body = chat_as(client, "acme", "I'm bob@example.org")

    # Then
    assert model.prompts[0][-1].text == "I'm [REDACTED_EMAIL]"
    assert body["reply"] == "Mail [REDACTED_EMAIL]"
    assert body["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]


@pytest.mark.parametrize(("tenant", "limit"), [("acme", 1), ("globex", 5)])
def test_tool_call_limit_follows_the_tenant(
    admin_client_for: ClientFor, tenant: str, limit: int
) -> None:
    # Given
    model = ScriptedChatModel(messages=endless_calculator_calls())
    client = configured(admin_client_for(model), ACME_POLICY)

    # When
    body = chat_as(client, tenant, "Keep adding")

    # Then
    assert f"at most {limit} tool calls" in body["reply"]
    assert body["guardrails"] == [STOPPED]
    assert model.calls == limit + 1


def test_zero_tool_calls_allows_a_plain_answer(admin_client_for: ClientFor) -> None:
    client = configured(admin_client_for(script("hi")), ACME_POLICY | {"max_tool_calls": 0})

    assert chat_as(client, "acme", "hello")["reply"] == "hi"


def test_system_prompt_is_added_to_the_agent_instructions(admin_client_for: ClientFor) -> None:
    # Given
    model = script("Acme here.")
    client = configured(admin_client_for(model), ACME_POLICY)

    # When
    chat_as(client, "acme", "hello")

    # Then
    system = model.prompts[0][0]
    assert system.type == "system"
    assert "Use the calculator tool" in system.text
    assert system.text.rstrip().endswith("Always answer as Acme's support desk.")


def test_no_system_prompt_leaves_the_instructions_alone(admin_client_for: ClientFor) -> None:
    # Given
    model = script("one", "two")
    client = configured(admin_client_for(model), ACME_POLICY)

    # When
    chat_as(client, "acme", "hello")
    chat_as(client, "globex", "hello")

    # Then
    acme_system, globex_system = model.prompts[0][0].text, model.prompts[1][0].text
    assert "Acme" not in globex_system
    assert acme_system.startswith(globex_system)


def test_subagent_runs_under_the_tenant_limit_without_its_prompt(
    admin_client_for: ClientFor,
) -> None:
    # Given the main agent delegates once and the subagent then loops on tools
    delegate = calling(
        tool_call("task", {"description": "add", "subagent_type": "general-purpose"}, "d1")
    )
    sums = (calling(tool_call("calculator", {"expression": "1"}, f"s{i}")) for i in range(9))
    model = script(delegate, *sums)
    client = configured(admin_client_for(model), ACME_POLICY | {"max_tool_calls": 2})

    # When
    body = chat_as(client, "acme", "Delegate some sums")

    # Then the delegation plus two subagent calls exceed the budget of two
    assert body["guardrails"] == [STOPPED]
    assert model.calls == 3
    subagent_system = model.prompts[1][0].text
    assert "Acme" not in subagent_system
