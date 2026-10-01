"""The admin API for tenant policies: authentication, storage round trips and validation."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.policies import GuardrailPolicy, InMemoryPolicyStore
from tests.fakes import ADMIN_KEY, DEFAULT_POLICY, ScriptedChatModel, make_policy

ADMIN = {"X-Admin-Key": ADMIN_KEY}
URL = "/tenants/acme/policy"
CUSTOM = make_policy(blocked_topics=["gambling"], redact_pii=False, max_tool_calls=2, system_prompt="Reply in French.")


@pytest.fixture
def admin_client(admin_key: str, replying_model: ScriptedChatModel) -> TestClient:
    return TestClient(create_app(replying_model))


def test_tenant_without_a_policy_gets_the_default(admin_client: TestClient) -> None:
    response = admin_client.get(URL, headers=ADMIN)

    assert response.status_code == 200
    assert response.json() == DEFAULT_POLICY


def test_put_stores_the_policy_and_returns_it(admin_client: TestClient) -> None:
    # Given
    put = admin_client.put(URL, json=CUSTOM, headers=ADMIN)

    # When
    fetched = admin_client.get(URL, headers=ADMIN)

    # Then
    assert put.status_code == 200
    assert put.json() == CUSTOM
    assert fetched.json() == CUSTOM


def test_put_replaces_the_whole_policy(admin_client: TestClient) -> None:
    # Given
    admin_client.put(URL, json=CUSTOM, headers=ADMIN)
    replacement = make_policy(blocked_topics=[], max_tool_calls=9)

    # When
    admin_client.put(URL, json=replacement, headers=ADMIN)

    # Then
    assert admin_client.get(URL, headers=ADMIN).json() == replacement


def test_delete_returns_the_tenant_to_the_default(admin_client: TestClient) -> None:
    # Given
    admin_client.put(URL, json=CUSTOM, headers=ADMIN)

    # When
    deleted = admin_client.delete(URL, headers=ADMIN)

    # Then
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert admin_client.get(URL, headers=ADMIN).json() == DEFAULT_POLICY


def test_delete_without_a_stored_policy_still_succeeds(admin_client: TestClient) -> None:
    assert admin_client.delete(URL, headers=ADMIN).status_code == 204


def test_policies_are_kept_per_tenant(admin_client: TestClient) -> None:
    # Given
    admin_client.put(URL, json=CUSTOM, headers=ADMIN)

    # When
    other = admin_client.get("/tenants/globex/policy", headers=ADMIN)

    # Then
    assert other.json() == DEFAULT_POLICY


def test_default_policy_reflects_environment_settings(
    monkeypatch: pytest.MonkeyPatch, admin_key: str, replying_model: ScriptedChatModel
) -> None:
    # Given
    monkeypatch.setenv("BLOCKED_TOPICS", "gambling")
    monkeypatch.setenv("MAX_TOOL_CALLS", "3")

    # When
    response = TestClient(create_app(replying_model)).get(URL, headers={"X-Admin-Key": admin_key})

    # Then
    assert response.json() == make_policy(blocked_topics=["gambling"], max_tool_calls=3)


@pytest.mark.parametrize(
    ("method", "body"),
    [("GET", None), ("PUT", CUSTOM), ("DELETE", None)],
)
@pytest.mark.parametrize("headers", [{}, {"X-Admin-Key": "wrong"}, {"X-Admin-Key": ""}])
def test_endpoints_refuse_a_missing_or_wrong_key(
    admin_client: TestClient, method: str, body: dict[str, object] | None, headers: dict[str, str]
) -> None:
    # When
    response = admin_client.request(method, URL, json=body, headers=headers)

    # Then
    assert response.status_code == 401
    assert admin_client.get(URL, headers=ADMIN).json() == DEFAULT_POLICY


def test_bad_key_is_refused_before_the_body_is_judged(admin_client: TestClient) -> None:
    response = admin_client.put(URL, json={"nonsense": True}, headers={"X-Admin-Key": "wrong"})

    assert response.status_code == 401


def test_endpoints_are_locked_when_no_admin_key_is_configured(replying_model: ScriptedChatModel) -> None:
    # Given
    client = TestClient(create_app(replying_model))

    # When
    responses = [client.get(URL, headers=ADMIN), client.get(URL, headers={"X-Admin-Key": ""})]

    # Then
    assert [response.status_code for response in responses] == [401, 401]


def test_key_is_read_when_the_app_is_created(
    monkeypatch: pytest.MonkeyPatch, admin_key: str, replying_model: ScriptedChatModel
) -> None:
    # Given
    client = TestClient(create_app(replying_model))

    # When
    monkeypatch.setenv("ADMIN_API_KEY", "rotated-later")

    # Then
    assert client.get(URL, headers={"X-Admin-Key": admin_key}).status_code == 200
    assert client.get(URL, headers={"X-Admin-Key": "rotated-later"}).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {key: value for key, value in CUSTOM.items() if key != "system_prompt"},
        {key: value for key, value in CUSTOM.items() if key != "max_tool_calls"},
        make_policy(blocked_topics="weapons"),
        make_policy(blocked_topics=["ok", 3]),
        make_policy(blocked_topics=["ok", "  "]),
        make_policy(redact_pii="yes"),
        make_policy(redact_pii=1),
        make_policy(max_tool_calls=0),
        make_policy(max_tool_calls=-2),
        make_policy(max_tool_calls="5"),
        make_policy(max_tool_calls=2.5),
        make_policy(max_tool_calls=True),
        make_policy(system_prompt=42),
        make_policy(redact_PII=False),
        [],
    ],
)
def test_invalid_policy_is_rejected(admin_client: TestClient, body: object) -> None:
    # When
    response = admin_client.put(URL, json=body, headers=ADMIN)

    # Then
    assert response.status_code == 422
    assert admin_client.get(URL, headers=ADMIN).json() == DEFAULT_POLICY


def test_non_json_policy_is_rejected(admin_client: TestClient) -> None:
    response = admin_client.put(URL, content=b"{", headers=ADMIN | {"content-type": "application/json"})

    assert response.status_code == 422


def test_an_injected_store_is_used(admin_key: str, replying_model: ScriptedChatModel) -> None:
    # Given
    store = InMemoryPolicyStore()
    client = TestClient(create_app(replying_model, policy_store=store))

    # When
    client.put(URL, json=CUSTOM, headers=ADMIN)

    # Then
    expected = GuardrailPolicy(
        blocked_topics=("gambling",), redact_pii=False, max_tool_calls=2, system_prompt="Reply in French."
    )
    assert asyncio.run(store.get_policy("acme")) == expected
