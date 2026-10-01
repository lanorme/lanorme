"""Stage 2 contract: per-tenant guardrail policies and their admin endpoints."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from fakes import ScriptedChatModel, read_call_text, read_human_texts
from support import (
    ADMIN_KEY,
    BLOCKED_BODY,
    DEFAULT_POLICY,
    PII,
    TOOL_LIMIT,
    build_headers,
    find_guardrails,
    build_id,
    post_chat,
    put_policy,
)

ADMIN = build_headers(admin=ADMIN_KEY)


def assert_policy(actual: dict, expected: dict) -> None:
    """Assert the four policy fields match; blocked-topic order is free."""
    assert sorted(actual["blocked_topics"]) == sorted(expected["blocked_topics"])
    assert actual["redact_pii"] is expected["redact_pii"]
    assert actual["max_tool_calls"] == expected["max_tool_calls"]
    assert actual["system_prompt"] == expected["system_prompt"]


@pytest.fixture
def default_tenant_policy(client: TestClient) -> Iterator[None]:
    """Clear any policy stored for the `default` tenant, before and after."""
    client.delete("/tenants/default/policy", headers=ADMIN)
    yield
    client.delete("/tenants/default/policy", headers=ADMIN)


@pytest.mark.parametrize("method", ["get", "put", "delete"])
@pytest.mark.parametrize("key", [None, "wrong-key"])
def test_tenant_endpoints_require_the_admin_key(
    client: TestClient,
    method: str,
    key: str | None,
) -> None:
    # Arrange
    tenant = build_id("t")
    kwargs = {"json": DEFAULT_POLICY} if method == "put" else {}
    # Act
    response = client.request(
        method.upper(),
        f"/tenants/{tenant}/policy",
        headers=build_headers(admin=key),
        **kwargs,
    )
    # Assert
    assert response.status_code == 401


def test_rejected_admin_request_changes_nothing(client: TestClient) -> None:
    # Arrange
    tenant = build_id("t")
    policy = {**DEFAULT_POLICY, "blocked_topics": ["bananas"]}
    # Act
    client.put(f"/tenants/{tenant}/policy", json=policy, headers=build_headers(admin="wrong-key"))
    assert_policy(client.get(f"/tenants/{tenant}/policy", headers=ADMIN).json(), DEFAULT_POLICY)


def test_get_unknown_tenant_returns_the_default_policy(client: TestClient) -> None:
    response = client.get(f"/tenants/{build_id('t')}/policy", headers=ADMIN)
    assert response.status_code == 200
    assert_policy(response.json(), DEFAULT_POLICY)


def test_put_stores_and_returns_the_policy(client: TestClient) -> None:
    # Arrange
    tenant = build_id("t")
    policy = {
        "blocked_topics": ["bananas"],
        "redact_pii": False,
        "max_tool_calls": 3,
        "system_prompt": "Be terse.",
    }
    # Act
    response = client.put(f"/tenants/{tenant}/policy", json=policy, headers=ADMIN)
    # Assert
    assert response.status_code == 200
    assert_policy(response.json(), policy)
    fetched = client.get(f"/tenants/{tenant}/policy", headers=ADMIN)
    assert fetched.status_code == 200
    assert_policy(fetched.json(), policy)


def test_put_replaces_an_earlier_policy(client: TestClient) -> None:
    # Arrange
    tenant = build_id("t")
    put_policy(client, tenant=tenant, blocked_topics=["bananas"])
    put_policy(client, tenant=tenant, blocked_topics=["cherries"], max_tool_calls=9)
    # Act
    fetched = client.get(f"/tenants/{tenant}/policy", headers=ADMIN).json()
    assert_policy(fetched, {**DEFAULT_POLICY, "blocked_topics": ["cherries"], "max_tool_calls": 9})


def test_delete_falls_back_to_the_default_policy(client: TestClient) -> None:
    # Arrange
    tenant = build_id("t")
    put_policy(client, tenant=tenant, blocked_topics=["bananas"], system_prompt="Be terse.")
    # Act
    response = client.delete(f"/tenants/{tenant}/policy", headers=ADMIN)
    # Assert
    assert response.status_code == 204
    assert_policy(client.get(f"/tenants/{tenant}/policy", headers=ADMIN).json(), DEFAULT_POLICY)


def test_deleted_policy_restores_default_chat(client: TestClient, model: ScriptedChatModel) -> None:
    # Act
    tenant = build_id("t")
    put_policy(client, tenant=tenant, blocked_topics=["bananas"])
    # Assert
    assert post_chat(client, message="I like bananas", tenant=tenant).status_code == 403
    client.delete(f"/tenants/{tenant}/policy", headers=ADMIN)
    assert post_chat(client, message="I like bananas", tenant=tenant).status_code == 200
    assert post_chat(client, message="I like weapons", tenant=tenant).status_code == 403


@pytest.mark.parametrize(
    "policy",
    [
        {**DEFAULT_POLICY, "blocked_topics": "weapons"},
        {**DEFAULT_POLICY, "blocked_topics": [1, 2]},
        {**DEFAULT_POLICY, "redact_pii": "maybe"},
        {**DEFAULT_POLICY, "max_tool_calls": "lots"},
        {**DEFAULT_POLICY, "system_prompt": 42},
        ["weapons"],
    ],
)
def test_invalid_policy_returns_422(client: TestClient, policy: object) -> None:
    # Arrange
    tenant = build_id("t")
    # Act
    response = client.put(f"/tenants/{tenant}/policy", json=policy, headers=ADMIN)
    # Assert
    assert response.status_code == 422
    assert_policy(client.get(f"/tenants/{tenant}/policy", headers=ADMIN).json(), DEFAULT_POLICY)


@pytest.mark.usefixtures("default_tenant_policy")
def test_missing_tenant_header_uses_the_default_tenant(client: TestClient) -> None:
    # Arrange
    put_policy(client, tenant="default", blocked_topics=["bananas"])
    # Act
    response = post_chat(client, message="I like bananas")
    # Assert
    assert response.status_code == 403
    assert {key: response.json().get(key) for key in BLOCKED_BODY} == BLOCKED_BODY
    assert post_chat(client, message="I like bananas", tenant=build_id("t")).status_code == 200


def test_blocklists_are_isolated_per_tenant(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    fruit, other = build_id("t"), build_id("t")
    put_policy(client, tenant=fruit, blocked_topics=["bananas"])
    # Act
    blocked = post_chat(client, message="Tell me about BANANAS", tenant=fruit)
    # Assert
    assert blocked.status_code == 403
    assert {key: blocked.json().get(key) for key in BLOCKED_BODY} == BLOCKED_BODY
    assert model.calls == []
    assert post_chat(client, message="Tell me about bananas", tenant=other).status_code == 200
    assert post_chat(client, message="Tell me about weapons", tenant=other).status_code == 403
    assert post_chat(client, message="Tell me about bananas").status_code == 200


def test_tenant_blocklist_replaces_the_default_list(client: TestClient) -> None:
    tenant = build_id("t")
    put_policy(client, tenant=tenant, blocked_topics=["bananas"])
    assert post_chat(client, message="Tell me about weapons", tenant=tenant).status_code == 200


def test_tenant_blocklist_matches_whole_words_only(client: TestClient) -> None:
    # Act
    tenant = build_id("t")
    put_policy(client, tenant=tenant, blocked_topics=["cats"])
    # Assert
    assert post_chat(client, message="I like cats", tenant=tenant).status_code == 403
    assert post_chat(client, message="I like concats", tenant=tenant).status_code == 200


def test_redact_pii_false_passes_raw_pii_both_ways(
    client: TestClient,
    model: ScriptedChatModel,
) -> None:
    # Arrange
    tenant = build_id("t")
    put_policy(client, tenant=tenant, redact_pii=False)
    model.add_replies("Sure, I will write to bob@example.com or call 555-123-4567.")
    # Act
    response = post_chat(client, message="My email is ada@example.com", tenant=tenant)
    # Assert
    assert response.status_code == 200
    assert any("ada@example.com" in text for text in read_human_texts(model.calls[0]))
    body = response.json()
    assert "bob@example.com" in body["reply"]
    assert "555-123-4567" in body["reply"]
    assert PII not in find_guardrails(body)


def test_redact_pii_true_tenant_still_redacts(client: TestClient, model: ScriptedChatModel) -> None:
    # Arrange
    tenant = build_id("t")
    put_policy(client, tenant=tenant, redact_pii=True, blocked_topics=["bananas"])
    model.add_replies("Write to bob@example.com.")
    # Act
    body = post_chat(client, message="My email is ada@example.com", tenant=tenant).json()
    # Assert
    assert not any("ada@example.com" in text for text in read_human_texts(model.calls[0]))
    assert "bob@example.com" not in body["reply"]
    assert PII in find_guardrails(body)


def test_system_prompt_reaches_the_model(client: TestClient, model: ScriptedChatModel) -> None:
    # Act
    tenant = build_id("t")
    marker = "Always answer like a pirate named Captain Quillfeather."
    put_policy(client, tenant=tenant, system_prompt=marker)
    # Assert
    assert post_chat(client, message="Hello there", tenant=tenant).status_code == 200
    assert marker in read_call_text(model.calls[0])


def test_system_prompt_stays_with_its_tenant(client: TestClient, model: ScriptedChatModel) -> None:
    # Act
    marker = "Always answer like a pirate named Captain Quillfeather."
    put_policy(client, tenant=build_id("t"), system_prompt=marker)
    # Assert
    assert post_chat(client, message="Hello there", tenant=build_id("t")).status_code == 200
    assert post_chat(client, message="Hello there").status_code == 200
    for call in model.calls:
        assert marker not in read_call_text(call)


@pytest.mark.parametrize("limit", [2, 8])
def test_max_tool_calls_follows_the_tenant(
    client: TestClient,
    model: ScriptedChatModel,
    limit: int,
) -> None:
    # Arrange
    tenant = build_id("t")
    put_policy(client, tenant=tenant, max_tool_calls=limit)
    model.loop_tool_calls()
    # Act
    response = post_chat(client, message="Plan my week.", tenant=tenant)
    # Assert
    assert response.status_code == 200
    assert TOOL_LIMIT in find_guardrails(response.json())
    # N tool calls take N model calls; the call asking for one more may or may
    # not be counted before the turn stops, so allow a little slack.
    assert limit <= len(model.calls) <= limit + 2, f"the model was called {len(model.calls)} times"
