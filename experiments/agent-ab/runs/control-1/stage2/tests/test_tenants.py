import asyncio

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent import GuardedAgent
from app.config import get_settings
from app.main import create_app
from app.policies import InMemoryPolicyStore, TenantPolicy
from tests.conftest import make_model, tool_call

ADMIN_KEY = "s3cret-admin-key"
ADMIN = {"X-Admin-Key": ADMIN_KEY}
DEFAULT_POLICY = {
    "blocked_topics": ["weapons", "malware"],
    "redact_pii": True,
    "max_tool_calls": 5,
    "system_prompt": None,
}


def policy(**overrides):
    return {**DEFAULT_POLICY, **overrides}


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)


@pytest.fixture
def make_client():
    def _make(responses=()):
        model = make_model(responses)
        return TestClient(create_app(model)), model

    return _make


def put_policy(client, tenant, body):
    resp = client.put(f"/tenants/{tenant}/policy", json=body, headers=ADMIN)
    assert resp.status_code == 200, resp.text
    return resp


def chat(client, message, tenant=None):
    headers = {"X-Tenant-ID": tenant} if tenant is not None else {}
    return client.post("/chat", json={"session_id": "s", "message": message}, headers=headers)


def system_texts(model):
    """The system message text of every model call, in order."""
    return [m.text for call in model.seen for m in call if isinstance(m, SystemMessage)]


def human_texts(model):
    return [m.content for call in model.seen for m in call if isinstance(m, HumanMessage)]


def calc_calls(n, start=0):
    return [
        AIMessage(content="", tool_calls=[tool_call("calculator", {"expression": "1+1"}, f"c{i}")])
        for i in range(start, start + n)
    ]


TOOL_LIMIT = [{"name": "tool_call_limit", "action": "stopped"}]
PII = [{"name": "pii_redaction", "action": "redacted"}]


# --- Policy CRUD ----------------------------------------------------------


def test_unknown_tenant_gets_default_policy(make_client):
    client, _ = make_client()
    resp = client.get("/tenants/acme/policy", headers=ADMIN)
    assert resp.status_code == 200
    assert resp.json() == DEFAULT_POLICY


def test_put_stores_and_returns_policy(make_client):
    client, _ = make_client()
    body = policy(blocked_topics=["crypto"], redact_pii=False, max_tool_calls=2, system_prompt="Be terse.")
    resp = client.put("/tenants/acme/policy", json=body, headers=ADMIN)
    assert resp.status_code == 200
    assert resp.json() == body
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == body
    # Other tenants are unaffected.
    assert client.get("/tenants/other/policy", headers=ADMIN).json() == DEFAULT_POLICY


def test_put_replaces_previous_policy(make_client):
    client, _ = make_client()
    put_policy(client, "acme", policy(max_tool_calls=1, system_prompt="first"))
    second = policy(blocked_topics=[], max_tool_calls=9)
    put_policy(client, "acme", second)
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == second


def test_put_normalises_blank_topics(make_client):
    client, _ = make_client()
    resp = put_policy(client, "acme", policy(blocked_topics=["  crypto ", "", "   "]))
    assert resp.json()["blocked_topics"] == ["crypto"]


def test_delete_falls_back_to_default(make_client):
    client, _ = make_client()
    put_policy(client, "acme", policy(max_tool_calls=1))
    resp = client.delete("/tenants/acme/policy", headers=ADMIN)
    assert resp.status_code == 204
    assert resp.content == b""
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == DEFAULT_POLICY


def test_delete_without_stored_policy_is_204(make_client):
    client, _ = make_client()
    assert client.delete("/tenants/nobody/policy", headers=ADMIN).status_code == 204


def test_tenant_ids_are_exact(make_client):
    client, _ = make_client()
    put_policy(client, "Acme", policy(max_tool_calls=1))
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == DEFAULT_POLICY


def test_default_policy_follows_agent_settings(make_client, monkeypatch):
    monkeypatch.setenv("AGENT_BLOCKED_TOPICS", "crypto")
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "3")
    get_settings.cache_clear()
    client, _ = make_client()
    assert client.get("/tenants/x/policy", headers=ADMIN).json() == policy(
        blocked_topics=["crypto"], max_tool_calls=3
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {k: v for k, v in DEFAULT_POLICY.items() if k != "system_prompt"},
        {k: v for k, v in DEFAULT_POLICY.items() if k != "blocked_topics"},
        policy(extra_field=1),
        policy(blocked_topics="weapons"),
        policy(blocked_topics=[1]),
        policy(blocked_topics=None),
        policy(redact_pii="no"),
        policy(redact_pii=0),
        policy(redact_pii=None),
        policy(max_tool_calls=-1),
        policy(max_tool_calls="5"),
        policy(max_tool_calls=2.5),
        policy(max_tool_calls=True),
        policy(max_tool_calls=None),
        policy(system_prompt=5),
        policy(system_prompt=["a"]),
        [DEFAULT_POLICY],
        "policy",
        None,
    ],
)
def test_invalid_policy_is_422_and_not_stored(make_client, body):
    client, _ = make_client()
    put_policy(client, "acme", policy(max_tool_calls=1))
    resp = client.put("/tenants/acme/policy", json=body, headers=ADMIN)
    assert resp.status_code == 422
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == policy(max_tool_calls=1)


@pytest.mark.parametrize("content", [b"not json", b"", b"{"])
def test_non_json_policy_is_422(make_client, content):
    client, _ = make_client()
    resp = client.put(
        "/tenants/acme/policy",
        content=content,
        headers={**ADMIN, "content-type": "application/json"},
    )
    assert resp.status_code == 422


# --- Admin authentication -------------------------------------------------


ENDPOINTS = [
    ("get", {}),
    ("put", {"json": DEFAULT_POLICY}),
    ("delete", {}),
]


@pytest.mark.parametrize("method, kwargs", ENDPOINTS)
@pytest.mark.parametrize("headers", [{}, {"X-Admin-Key": "wrong"}, {"X-Admin-Key": ""}])
def test_admin_endpoints_require_key(make_client, method, kwargs, headers):
    client, _ = make_client()
    resp = client.request(method, "/tenants/acme/policy", headers=headers, **kwargs)
    assert resp.status_code == 401


def test_unauthorised_put_does_not_store(make_client):
    client, _ = make_client()
    client.put("/tenants/acme/policy", json=policy(max_tool_calls=1), headers={"X-Admin-Key": "x"})
    assert client.get("/tenants/acme/policy", headers=ADMIN).json() == DEFAULT_POLICY


def test_unauthorised_delete_does_not_delete(make_client):
    client, _ = make_client()
    put_policy(client, "acme", policy(max_tool_calls=1))
    assert client.delete("/tenants/acme/policy").status_code == 401
    assert client.get("/tenants/acme/policy", headers=ADMIN).json()["max_tool_calls"] == 1


@pytest.mark.parametrize("content", [b"not json", b'{"bad": true}'])
def test_auth_is_checked_before_body(make_client, content):
    client, _ = make_client()
    resp = client.put(
        "/tenants/acme/policy", content=content, headers={"content-type": "application/json"}
    )
    assert resp.status_code == 401


def test_admin_key_is_read_when_app_is_created(make_client, monkeypatch):
    client, _ = make_client()
    monkeypatch.setenv("ADMIN_API_KEY", "rotated")
    assert client.get("/tenants/a/policy", headers=ADMIN).status_code == 200
    assert client.get("/tenants/a/policy", headers={"X-Admin-Key": "rotated"}).status_code == 401


@pytest.mark.parametrize("key", ["", None])
def test_unset_admin_key_locks_admin_endpoints(make_client, monkeypatch, key):
    if key is None:
        monkeypatch.delenv("ADMIN_API_KEY")
    else:
        monkeypatch.setenv("ADMIN_API_KEY", key)
    client, _ = make_client()
    assert client.get("/tenants/a/policy").status_code == 401
    assert client.get("/tenants/a/policy", headers={"X-Admin-Key": ""}).status_code == 401
    assert client.get("/tenants/a/policy", headers=ADMIN).status_code == 401


def test_chat_does_not_need_admin_key(make_client):
    client, _ = make_client(["hi"])
    assert chat(client, "hello", tenant="acme").status_code == 200


# --- Chat applies the tenant's policy ------------------------------------


def test_tenant_blocked_topics(make_client):
    client, model = make_client(["ok weapons", "ok crypto"])
    put_policy(client, "acme", policy(blocked_topics=["crypto", "dark web"]))

    resp = chat(client, "Crypto tips?", tenant="acme")
    assert resp.status_code == 403
    assert resp.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert chat(client, "the DARK WEB", tenant="acme").status_code == 403
    assert model.seen == []
    # acme replaced the default list, so "weapons" is allowed for it...
    assert chat(client, "weapons history", tenant="acme").json()["reply"] == "ok weapons"
    # ...but not for anyone else, while "crypto" is.
    assert chat(client, "weapons history", tenant="other").status_code == 403
    assert chat(client, "weapons history").status_code == 403
    assert chat(client, "crypto tips", tenant="other").json()["reply"] == "ok crypto"


def test_empty_blocklist_blocks_nothing(make_client):
    client, _ = make_client(["sure"])
    put_policy(client, "acme", policy(blocked_topics=[]))
    assert chat(client, "malware and weapons", tenant="acme").status_code == 200


def test_redact_pii_false_passes_message_and_reply_unchanged(make_client):
    reply = "Reach support@corp.com or 555-123-4567."
    client, model = make_client([reply])
    put_policy(client, "acme", policy(redact_pii=False))
    msg = "I'm jane@example.com, phone +44 20 7946 0958."
    resp = chat(client, msg, tenant="acme")
    assert resp.status_code == 200
    assert resp.json() == {"session_id": "s", "reply": reply, "guardrails": []}
    assert human_texts(model) == [msg]


def test_redact_pii_true_still_redacts_for_tenant(make_client):
    client, model = make_client(["Mail x@y.io"])
    put_policy(client, "acme", policy(blocked_topics=["crypto"], redact_pii=True))
    resp = chat(client, "I am a@b.com", tenant="acme")
    assert resp.json()["reply"] == "Mail [REDACTED_EMAIL]"
    assert resp.json()["guardrails"] == PII
    assert human_texts(model) == ["I am [REDACTED_EMAIL]"]


def test_redaction_is_per_tenant(make_client):
    client, model = make_client(["one a@b.com", "two a@b.com"])
    put_policy(client, "raw", policy(redact_pii=False))
    assert chat(client, "me: c@d.com", tenant="raw").json()["reply"] == "one a@b.com"
    assert chat(client, "me: c@d.com", tenant="other").json()["reply"] == "two [REDACTED_EMAIL]"
    assert human_texts(model) == ["me: c@d.com", "me: [REDACTED_EMAIL]"]


def test_redact_pii_false_keeps_other_guardrails(make_client):
    client, model = make_client(calc_calls(3))
    put_policy(client, "acme", policy(redact_pii=False, max_tool_calls=2))
    assert chat(client, "malware", tenant="acme").status_code == 403
    resp = chat(client, "I am a@b.com, compute", tenant="acme")
    assert resp.json()["guardrails"] == TOOL_LIMIT
    assert human_texts(model)[0] == "I am a@b.com, compute"


def test_tenant_tool_limit_lower(make_client):
    client, model = make_client([*calc_calls(3), "never reached"])
    put_policy(client, "acme", policy(max_tool_calls=2))
    body = chat(client, "go", tenant="acme").json()
    assert body["guardrails"] == TOOL_LIMIT
    assert "2 tool calls" in body["reply"]
    assert len(model.seen) == 3
    assert len([m for m in model.seen[-1] if isinstance(m, ToolMessage)]) == 2


def test_tenant_tool_limit_higher(make_client):
    client, _ = make_client([*calc_calls(8), "done", *calc_calls(6), "unreached"])
    put_policy(client, "acme", policy(max_tool_calls=8))
    body = chat(client, "go", tenant="acme").json()
    assert (body["reply"], body["guardrails"]) == ("done", [])
    # The default tenant still stops at 5.
    assert chat(client, "go").json()["guardrails"] == TOOL_LIMIT


def test_tenant_tool_limit_zero(make_client):
    client, model = make_client([*calc_calls(1), "plain answer"])
    put_policy(client, "acme", policy(max_tool_calls=0))
    body = chat(client, "go", tenant="acme").json()
    assert body["guardrails"] == TOOL_LIMIT
    assert "0 tool calls" in body["reply"]
    assert not any(isinstance(m, ToolMessage) for call in model.seen for m in call)
    # Answering without tools is fine.
    assert chat(client, "hi", tenant="acme").json() == {
        "session_id": "s", "reply": "plain answer", "guardrails": []
    }


def test_parallel_tool_calls_against_tenant_limit(make_client):
    batch = AIMessage(
        content="", tool_calls=[tool_call("calculator", {"expression": "1"}, f"p{i}") for i in range(3)]
    )
    client, model = make_client([batch, "unreached"])
    put_policy(client, "acme", policy(max_tool_calls=2))
    assert chat(client, "go", tenant="acme").json()["guardrails"] == TOOL_LIMIT
    assert len(model.seen) == 1


def test_tenant_system_prompt_added_to_instructions(make_client):
    client, model = make_client(["one", "two"])
    put_policy(client, "acme", policy(system_prompt="Always answer in French."))
    chat(client, "hi", tenant="acme")
    chat(client, "hi", tenant="other")
    acme, other = system_texts(model)
    base = get_settings().system_prompt
    # Added to, not replacing, the agent's instructions (which include deepagents' own).
    assert acme.startswith(base)
    assert acme.endswith("\n\nAlways answer in French.")
    assert acme.removesuffix("\n\nAlways answer in French.") == other
    assert "French" not in other


def test_tenant_system_prompt_applies_to_every_model_call_in_turn(make_client):
    client, model = make_client([*calc_calls(2), "done"])
    put_policy(client, "acme", policy(system_prompt="TENANT-RULE"))
    assert chat(client, "go", tenant="acme").json()["reply"] == "done"
    texts = system_texts(model)
    assert len(texts) == 3
    assert all(t.count("TENANT-RULE") == 1 for t in texts)


def test_empty_system_prompt_adds_nothing(make_client):
    client, model = make_client(["a", "b"])
    put_policy(client, "acme", policy(system_prompt=""))
    chat(client, "hi", tenant="acme")
    chat(client, "hi")
    assert system_texts(model)[0] == system_texts(model)[1]


def test_policy_changes_apply_to_next_request(make_client):
    client, model = make_client(["r1", "r2", "r3"])
    assert chat(client, "crypto", tenant="acme").status_code == 200
    put_policy(client, "acme", policy(blocked_topics=["crypto"], system_prompt="RULE"))
    assert chat(client, "crypto", tenant="acme").status_code == 403
    assert chat(client, "hello", tenant="acme").status_code == 200
    client.delete("/tenants/acme/policy", headers=ADMIN)
    assert chat(client, "crypto", tenant="acme").status_code == 200
    assert ["RULE" in t for t in system_texts(model)] == [False, True, False]


def test_missing_or_empty_tenant_header_uses_default_tenant(make_client):
    client, _ = make_client(["unused"])
    put_policy(client, "default", policy(blocked_topics=["crypto"]))
    assert chat(client, "crypto").status_code == 403
    assert chat(client, "crypto", tenant="").status_code == 403
    assert chat(client, "crypto", tenant="acme").status_code == 200


# --- Agent internals ------------------------------------------------------


def test_graph_is_reused_per_tool_limit():
    agent = GuardedAgent(make_model([]), get_settings())
    assert agent.graph_for(3) is agent.graph_for(3)
    assert agent.graph_for(3) is not agent.graph_for(4)


def test_concurrent_turns_keep_tenant_prompts_apart():
    model = make_model([f"r{i}" for i in range(20)])
    agent = GuardedAgent(model, get_settings())

    async def run_all():
        return await asyncio.gather(
            *(
                agent.run_turn("hi", TenantPolicy(**policy(system_prompt=f"TENANT-{i % 2}")))
                for i in range(20)
            )
        )

    asyncio.run(run_all())
    texts = system_texts(model)
    assert len(texts) == 20
    for text in texts:
        assert ("TENANT-0" in text) != ("TENANT-1" in text)
    assert sum("TENANT-0" in t for t in texts) == 10


# --- Store ---------------------------------------------------------------


def test_in_memory_store():
    store = InMemoryPolicyStore()
    p = TenantPolicy(**DEFAULT_POLICY)

    async def scenario():
        assert await store.get("a") is None
        await store.put("a", p)
        assert await store.get("a") == p
        assert await store.get("b") is None
        await store.delete("a")
        await store.delete("a")
        assert await store.get("a") is None

    asyncio.run(scenario())


def test_store_is_replaceable(make_client):
    class RecordingStore(InMemoryPolicyStore):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def get(self, tenant_id):
            self.calls.append(("get", tenant_id))
            return await super().get(tenant_id)

    client, _ = make_client(["unused"])
    store = RecordingStore()
    client.app.state.policies.store = store
    put_policy(client, "acme", policy(blocked_topics=["crypto"]))
    assert chat(client, "crypto", tenant="acme").status_code == 403
    assert ("get", "acme") in store.calls
