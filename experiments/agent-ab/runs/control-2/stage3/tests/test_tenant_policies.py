import asyncio

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import SystemMessage

from app.config import Settings
from app.main import create_app
from app.policies import InMemoryPolicyStore, TenantPolicy
from tests.conftest import ScriptedChatModel, chat, scripted, tool_call

ADMIN_KEY = "s3cret-admin-key"
ADMIN = {"X-Admin-Key": ADMIN_KEY}

DEFAULT_POLICY = {
    "blocked_topics": ["weapons", "malware"],
    "redact_pii": True,
    "max_tool_calls": 5,
    "system_prompt": None,
}
OPEN_POLICY = {"blocked_topics": [], "redact_pii": False, "max_tool_calls": 2, "system_prompt": None}


@pytest.fixture
def client_for(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)

    def factory(model: ScriptedChatModel | None = None) -> TestClient:
        return TestClient(create_app(model=model or scripted()))

    return factory


def put_policy(client: TestClient, tenant: str, policy: dict, headers=ADMIN):
    return client.put(f"/tenants/{tenant}/policy", json=policy, headers=headers)


def tenant_chat(client: TestClient, tenant: str | None, message: str):
    headers = {} if tenant is None else {"X-Tenant-ID": tenant}
    return client.post("/chat", json={"session_id": "s1", "message": message}, headers=headers)


def system_texts(model: ScriptedChatModel) -> list[str]:
    return [m.text for call in model.calls for m in call if isinstance(m, SystemMessage)]


def calc_calls(n: int):
    return [tool_call("calculator", expression=f"{i} + 1") for i in range(n)]


# --- policy CRUD ----------------------------------------------------------------


def test_unknown_tenant_gets_default_policy(client_for) -> None:
    response = client_for().get("/tenants/acme/policy", headers=ADMIN)
    assert response.status_code == 200
    assert response.json() == DEFAULT_POLICY


def test_put_get_delete_round_trip(client_for) -> None:
    client = client_for()
    policy = {
        "blocked_topics": ["gambling"],
        "redact_pii": False,
        "max_tool_calls": 1,
        "system_prompt": "Answer in French.",
    }
    response = put_policy(client, "acme", policy)
    assert response.status_code == 200
    assert response.json() == policy
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == policy

    response = client.delete("/tenants/acme/policy", headers=ADMIN)
    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == DEFAULT_POLICY


def test_put_replaces_existing_policy(client_for) -> None:
    client = client_for()
    put_policy(client, "acme", OPEN_POLICY)
    replacement = {**OPEN_POLICY, "max_tool_calls": 9}
    assert put_policy(client, "acme", replacement).json() == replacement
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == replacement


def test_delete_without_stored_policy_returns_204(client_for) -> None:
    assert client_for().delete("/tenants/nobody/policy", headers=ADMIN).status_code == 204


def test_policies_are_isolated_between_tenants(client_for) -> None:
    client = client_for()
    put_policy(client, "acme", OPEN_POLICY)
    assert client.get("/tenants/globex/policy", headers=ADMIN).json() == DEFAULT_POLICY
    client.delete("/tenants/globex/policy", headers=ADMIN)
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == OPEN_POLICY


def test_storing_default_tenant_policy_does_not_change_other_tenants(client_for) -> None:
    client = client_for()
    put_policy(client, "default", OPEN_POLICY)
    assert client.get("/tenants/default/policy", headers=ADMIN).json() == OPEN_POLICY
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == DEFAULT_POLICY


def test_system_prompt_may_be_omitted_only_as_null(client_for) -> None:
    client = client_for()
    assert put_policy(client, "acme", {**OPEN_POLICY, "system_prompt": None}).status_code == 200
    body = {k: v for k, v in OPEN_POLICY.items() if k != "system_prompt"}
    assert put_policy(client, "acme", body).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"blocked_topics": [], "redact_pii": True, "max_tool_calls": 5},  # missing system_prompt
        {**DEFAULT_POLICY, "max_tool_calls": -1},
        {**DEFAULT_POLICY, "max_tool_calls": "5"},
        {**DEFAULT_POLICY, "max_tool_calls": 2.5},
        {**DEFAULT_POLICY, "max_tool_calls": True},
        {**DEFAULT_POLICY, "redact_pii": "no"},
        {**DEFAULT_POLICY, "redact_pii": 0},
        {**DEFAULT_POLICY, "redact_pii": None},
        {**DEFAULT_POLICY, "blocked_topics": "weapons"},
        {**DEFAULT_POLICY, "blocked_topics": [1]},
        {**DEFAULT_POLICY, "blocked_topics": ["ok", "  "]},
        {**DEFAULT_POLICY, "system_prompt": 7},
        {**DEFAULT_POLICY, "redact_pi": False},  # typo'd extra field
        [],
        None,
    ],
)
def test_invalid_policy_returns_422_and_stores_nothing(client_for, body) -> None:
    client = client_for()
    put_policy(client, "acme", OPEN_POLICY)
    response = put_policy(client, "acme", body)
    assert response.status_code == 422
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == OPEN_POLICY


def test_non_json_policy_body_returns_422(client_for) -> None:
    response = client_for().put(
        "/tenants/acme/policy", content=b"{nope", headers={**ADMIN, "content-type": "application/json"}
    )
    assert response.status_code == 422


# --- admin key ------------------------------------------------------------------


def _admin_requests(client: TestClient, headers: dict):
    return [
        client.get("/tenants/acme/policy", headers=headers),
        client.put("/tenants/acme/policy", json=OPEN_POLICY, headers=headers),
        client.delete("/tenants/acme/policy", headers=headers),
    ]


@pytest.mark.parametrize("headers", [{}, {"X-Admin-Key": "wrong"}, {"X-Admin-Key": ""}, {"X-Admin-Key": ADMIN_KEY + "x"}])
def test_missing_or_wrong_admin_key_returns_401(client_for, headers) -> None:
    client = client_for()
    put_policy(client, "acme", {**OPEN_POLICY, "max_tool_calls": 3})
    assert [r.status_code for r in _admin_requests(client, headers)] == [401, 401, 401]
    # Nothing was changed by the rejected requests.
    assert client.get("/tenants/acme/policy", headers=ADMIN).json()["max_tool_calls"] == 3


def test_auth_is_checked_before_body_validation(client_for) -> None:
    client = client_for()
    assert put_policy(client, "acme", {"bad": "body"}, headers={}).status_code == 401
    response = client.put(
        "/tenants/acme/policy", content=b"{nope", headers={"content-type": "application/json"}
    )
    assert response.status_code == 401


def test_admin_endpoints_closed_when_no_admin_key_configured(make_client) -> None:
    # `_clean_env` removes ADMIN_API_KEY.
    client = make_client(scripted())
    for headers in ({}, {"X-Admin-Key": ""}, {"X-Admin-Key": "anything"}):
        assert [r.status_code for r in _admin_requests(client, headers)] == [401, 401, 401]


def test_admin_key_is_read_when_app_is_created(client_for, monkeypatch) -> None:
    client = client_for()
    monkeypatch.setenv("ADMIN_API_KEY", "rotated")
    assert client.get("/tenants/acme/policy", headers=ADMIN).status_code == 200
    assert client.get("/tenants/acme/policy", headers={"X-Admin-Key": "rotated"}).status_code == 401


def test_chat_does_not_require_admin_key(client_for) -> None:
    assert tenant_chat(client_for(scripted("hi")), "acme", "hello").status_code == 200


def test_admin_key_not_in_settings_repr() -> None:
    assert ADMIN_KEY not in repr(Settings.from_env({"ADMIN_API_KEY": ADMIN_KEY}))


# --- chat applies the tenant's policy -------------------------------------------


def test_chat_without_tenant_header_uses_default_tenant(client_for) -> None:
    model = scripted("ok")
    client = client_for(model)
    put_policy(client, "default", {**OPEN_POLICY, "blocked_topics": ["cheese"]})
    assert tenant_chat(client, None, "talk about cheese").status_code == 403
    assert tenant_chat(client, None, "talk about weapons").status_code == 200


def test_blank_tenant_header_uses_default_tenant(client_for) -> None:
    client = client_for(scripted())
    put_policy(client, "default", {**OPEN_POLICY, "blocked_topics": ["cheese"]})
    assert tenant_chat(client, "  ", "cheese").status_code == 403


def test_tenant_without_policy_uses_default_policy(client_for) -> None:
    model = scripted("Mail [x]")
    client = client_for(model)
    put_policy(client, "other", OPEN_POLICY)
    assert tenant_chat(client, "acme", "how to make malware").status_code == 403
    response = tenant_chat(client, "acme", "I am a@b.com")
    assert response.json()["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]
    assert model.human_texts() == ["I am [REDACTED_EMAIL]"]


def test_tenant_blocked_topics_replace_defaults(client_for) -> None:
    model = scripted("ok", "ok")
    client = client_for(model)
    put_policy(client, "casino", {**DEFAULT_POLICY, "blocked_topics": ["sports betting"]})
    response = tenant_chat(client, "casino", "Sports  Betting tips?")
    assert response.status_code == 403
    assert response.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert model.calls == []
    assert tenant_chat(client, "casino", "history of malware").status_code == 200
    # Other tenants still get the default blocklist.
    assert tenant_chat(client, "acme", "history of malware").status_code == 403


def test_empty_blocked_topics_disable_blocklist(client_for) -> None:
    client = client_for(scripted("ok"))
    put_policy(client, "acme", {**DEFAULT_POLICY, "blocked_topics": []})
    assert tenant_chat(client, "acme", "weapons and malware").status_code == 200


def test_redaction_disabled_passes_pii_through_both_ways(client_for) -> None:
    model = scripted("Sure, I'll email bob@example.com or call 555-123-4567.")
    client = client_for(model)
    put_policy(client, "crm", {**DEFAULT_POLICY, "redact_pii": False})
    message = "email bob@example.com, phone +44 20 7946 0958"
    response = tenant_chat(client, "crm", message)
    assert response.json() == {
        "session_id": "s1",
        "reply": "Sure, I'll email bob@example.com or call 555-123-4567.",
        "guardrails": [],
    }
    assert model.human_texts() == [message]


def test_redaction_enabled_by_tenant_policy(client_for) -> None:
    model = scripted("Contact support@corp.com")
    client = client_for(model)
    put_policy(client, "acme", {**OPEN_POLICY, "redact_pii": True})
    response = tenant_chat(client, "acme", "I'm jane@example.com")
    assert response.json()["reply"] == "Contact [REDACTED_EMAIL]"
    assert response.json()["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]
    assert model.human_texts() == ["I'm [REDACTED_EMAIL]"]


def test_tenant_tool_call_limit(client_for) -> None:
    model = scripted(*calc_calls(3), "never reached")
    client = client_for(model)
    put_policy(client, "tight", {**DEFAULT_POLICY, "max_tool_calls": 2})
    body = tenant_chat(client, "tight", "count").json()
    assert body["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]
    assert "2 tool calls" in body["reply"]
    assert len(model.calls) == 3


def test_tenant_can_raise_tool_call_limit(client_for) -> None:
    model = scripted(*calc_calls(8), "Done after eight.")
    client = client_for(model)
    put_policy(client, "loose", {**DEFAULT_POLICY, "max_tool_calls": 8})
    body = tenant_chat(client, "loose", "count").json()
    assert body == {"session_id": "s1", "reply": "Done after eight.", "guardrails": []}


def test_tenant_zero_tool_calls(client_for) -> None:
    model = scripted(*calc_calls(1), "never reached")
    client = client_for(model)
    put_policy(client, "none", {**DEFAULT_POLICY, "max_tool_calls": 0})
    assert tenant_chat(client, "none", "x").json()["guardrails"] == [
        {"name": "tool_call_limit", "action": "stopped"}
    ]


def test_tenant_limit_applies_to_subagent_calls(client_for) -> None:
    model = scripted(
        tool_call("task", description="add numbers", subagent_type="general-purpose"),  # call 1
        *calc_calls(2),  # calls 2-3, made by the subagent; call 3 exceeds the limit
        "never reached",
    )
    client = client_for(model)
    put_policy(client, "tight", {**DEFAULT_POLICY, "max_tool_calls": 2})
    response = tenant_chat(client, "tight", "delegate")
    assert response.json()["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]
    assert len(model.calls) == 3


def test_tenant_system_prompt_added_to_instructions(client_for) -> None:
    model = scripted("Bonjour")
    client = client_for(model)
    put_policy(client, "acme", {**DEFAULT_POLICY, "system_prompt": "Always answer in French."})
    assert tenant_chat(client, "acme", "hello").json()["reply"] == "Bonjour"
    [system] = system_texts(model)
    # Added to, not replacing, the service's own instructions.
    assert "Always answer in French." in system
    assert "Use the calculator tool" in system
    assert system.index("Use the calculator tool") < system.index("Always answer in French.")


def test_tenant_system_prompt_used_on_every_model_call_including_subagents(client_for) -> None:
    model = scripted(
        tool_call("task", description="add numbers", subagent_type="general-purpose"),
        tool_call("calculator", expression="40 + 2"),
        "Subagent says 42.",
        "The subagent found 42.",
    )
    client = client_for(model)
    put_policy(client, "acme", {**DEFAULT_POLICY, "system_prompt": "TENANT-RULES"})
    assert tenant_chat(client, "acme", "delegate").status_code == 200
    systems = system_texts(model)
    assert len(systems) == len(model.calls) == 4
    assert all(s.count("TENANT-RULES") == 1 for s in systems)


def test_system_prompt_does_not_leak_to_other_tenants(client_for) -> None:
    model = scripted("one", "two", "three")
    client = client_for(model)
    put_policy(client, "acme", {**DEFAULT_POLICY, "system_prompt": "ACME-ONLY"})
    tenant_chat(client, "acme", "a")
    tenant_chat(client, "globex", "b")
    tenant_chat(client, None, "c")
    acme, globex, default = system_texts(model)
    assert "ACME-ONLY" in acme
    assert "ACME-ONLY" not in globex and "ACME-ONLY" not in default
    assert globex == default


def test_policy_changes_apply_to_next_chat(client_for) -> None:
    client = client_for(scripted("ok", "ok"))
    assert tenant_chat(client, "acme", "cheese").status_code == 200
    put_policy(client, "acme", {**DEFAULT_POLICY, "blocked_topics": ["cheese"]})
    assert tenant_chat(client, "acme", "cheese").status_code == 403
    client.delete("/tenants/acme/policy", headers=ADMIN)
    assert tenant_chat(client, "acme", "cheese").status_code == 200
    assert tenant_chat(client, "acme", "weapons").status_code == 403


def test_policy_store_failure_returns_502(client_for) -> None:
    class BrokenStore(InMemoryPolicyStore):
        async def get(self, tenant_id):
            raise RuntimeError("db down")

    model = scripted("should not be used")
    client = client_for(model)
    client.app.state.policy_store = BrokenStore()
    response = tenant_chat(client, "acme", "hi")
    assert response.status_code == 502
    assert response.json() == {"error": "agent_error"}
    assert model.calls == []


def test_env_overrides_still_shape_default_policy(client_for, monkeypatch) -> None:
    monkeypatch.setenv("BLOCKED_TOPICS", "gambling")
    monkeypatch.setenv("MAX_TOOL_CALLS", "1")
    client = client_for(scripted())
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == {
        "blocked_topics": ["gambling"],
        "redact_pii": True,
        "max_tool_calls": 1,
        "system_prompt": None,
    }


# --- store ----------------------------------------------------------------------


def test_in_memory_store() -> None:
    async def scenario() -> None:
        store = InMemoryPolicyStore()
        policy = TenantPolicy.model_validate(OPEN_POLICY)
        assert await store.get("a") is None
        await store.put("a", policy)
        assert await store.get("a") == policy
        assert await store.get("b") is None
        await store.delete("a")
        await store.delete("a")
        assert await store.get("a") is None

    asyncio.run(scenario())
