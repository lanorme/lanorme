"""Per-tenant policies: the admin API and how /chat applies a tenant's policy."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main
import app.tools
from app.guardrails import LIMIT_REACHED_REPLY
from app.policies import InMemoryPolicyStore, Policy
from tests.conftest import ADMIN_KEY
from tests.fakes import ai, endless, scripted, tool_call

ADMIN = {"X-Admin-Key": ADMIN_KEY}
PII = {"name": "pii_redaction", "action": "redacted"}
LIMIT = {"name": "tool_call_limit", "action": "stopped"}
DEFAULT_POLICY = {
    "blocked_topics": ["weapons", "malware"],
    "redact_pii": True,
    "max_tool_calls": 5,
    "system_prompt": None,
}


def policy(**overrides):
    return {**DEFAULT_POLICY, **overrides}


def put(client, tenant, body, headers=ADMIN):
    return client.put(f"/tenants/{tenant}/policy", json=body, headers=headers)


def get(client, tenant, headers=ADMIN):
    return client.get(f"/tenants/{tenant}/policy", headers=headers)


def delete(client, tenant, headers=ADMIN):
    return client.delete(f"/tenants/{tenant}/policy", headers=headers)


def chat(client, message, tenant=None, session_id="s1"):
    headers = {} if tenant is None else {"X-Tenant-ID": tenant}
    return client.post(
        "/chat", json={"session_id": session_id, "message": message}, headers=headers
    )


def system_texts(model):
    return [m.text for msgs in model.received for m in msgs if m.type == "system"]


@pytest.fixture
def calc_counter(monkeypatch):
    calls = []
    real = app.tools.evaluate

    def counting(expression):
        calls.append(expression)
        return real(expression)

    monkeypatch.setattr(app.tools, "evaluate", counting)
    return calls


# ------------------------------------------------------------------ admin auth


ENDPOINTS = [
    lambda c, h: put(c, "acme", policy(), headers=h),
    lambda c, h: get(c, "acme", headers=h),
    lambda c, h: delete(c, "acme", headers=h),
]


@pytest.mark.parametrize("call", ENDPOINTS, ids=["put", "get", "delete"])
@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Admin-Key": ""}, {"X-Admin-Key": "wrong"}, {"X-Admin-Key": ADMIN_KEY + "x"}],
    ids=["missing", "empty", "wrong", "prefix"],
)
def test_admin_endpoints_require_key(make_client, call, headers):
    client = make_client(scripted())
    assert call(client, headers).status_code == 401


def test_rejected_put_does_not_store(make_client):
    client = make_client(scripted())
    put(client, "acme", policy(max_tool_calls=1), headers={"X-Admin-Key": "wrong"})
    assert get(client, "acme").json() == DEFAULT_POLICY


def test_auth_checked_before_body_validation(make_client):
    client = make_client(scripted())
    assert put(client, "acme", {"junk": 1}, headers={}).status_code == 401


@pytest.mark.parametrize("call", ENDPOINTS, ids=["put", "get", "delete"])
@pytest.mark.parametrize("key", [None, ""])
def test_without_configured_key_admin_is_closed(make_client, call, key):
    client = make_client(scripted(), admin_api_key=key)
    for headers in ({}, {"X-Admin-Key": ""}, {"X-Admin-Key": "None"}):
        assert call(client, headers).status_code == 401


def test_admin_key_read_from_env_at_creation(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "from-env")
    client = TestClient(app.main.create_app(scripted()))
    monkeypatch.setenv("ADMIN_API_KEY", "changed-later")
    assert get(client, "acme", headers={"X-Admin-Key": "from-env"}).status_code == 200
    assert get(client, "acme", headers={"X-Admin-Key": "changed-later"}).status_code == 401


def test_chat_needs_no_admin_key(make_client):
    assert chat(make_client(scripted(ai("hi"))), "hello").status_code == 200


# ------------------------------------------------------------------ policy CRUD


def test_get_without_stored_policy_is_default(make_client):
    r = get(make_client(scripted()), "nobody")
    assert r.status_code == 200
    assert r.json() == DEFAULT_POLICY


def test_put_stores_and_returns_policy(make_client):
    client = make_client(scripted())
    body = policy(blocked_topics=["gambling"], redact_pii=False, max_tool_calls=2,
                  system_prompt="Answer in French.")
    r = put(client, "acme", body)
    assert r.status_code == 200
    assert r.json() == body
    assert get(client, "acme").json() == body
    assert get(client, "other").json() == DEFAULT_POLICY


def test_put_replaces_whole_policy(make_client):
    client = make_client(scripted())
    put(client, "acme", policy(system_prompt="x", max_tool_calls=1))
    put(client, "acme", policy(blocked_topics=[]))
    assert get(client, "acme").json() == policy(blocked_topics=[])


def test_delete_falls_back_to_default(make_client):
    client = make_client(scripted())
    put(client, "acme", policy(max_tool_calls=0))
    r = delete(client, "acme")
    assert r.status_code == 204
    assert r.content == b""
    assert get(client, "acme").json() == DEFAULT_POLICY


def test_delete_without_stored_policy_is_204(make_client):
    assert delete(make_client(scripted()), "nobody").status_code == 204


def test_put_normalises_topics_and_prompt(make_client):
    client = make_client(scripted())
    r = put(client, "acme", policy(blocked_topics=[" Gambling ", "", "gambling", "  "],
                                   system_prompt="   "))
    assert r.json()["blocked_topics"] == ["Gambling"]
    assert r.json()["system_prompt"] is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        "policy",
        {k: v for k, v in DEFAULT_POLICY.items() if k != "redact_pii"},
        {k: v for k, v in DEFAULT_POLICY.items() if k != "system_prompt"},
        policy(extra_field=1),
        policy(blocked_topics="weapons"),
        policy(blocked_topics=[1, 2]),
        policy(blocked_topics=None),
        policy(blocked_topics=["x"] * 101),
        policy(blocked_topics=["x" * 101]),
        policy(redact_pii="yes"),
        policy(redact_pii=None),
        policy(redact_pii=1),
        policy(max_tool_calls=-1),
        policy(max_tool_calls=101),
        policy(max_tool_calls="5"),
        policy(max_tool_calls=2.5),
        policy(max_tool_calls=True),
        policy(max_tool_calls=None),
        policy(system_prompt=42),
        policy(system_prompt="x" * 8001),
    ],
)
def test_invalid_policy_is_422_and_not_stored(make_client, body):
    client = make_client(scripted())
    put(client, "acme", policy(max_tool_calls=3))
    assert put(client, "acme", body).status_code == 422
    assert get(client, "acme").json() == policy(max_tool_calls=3)


def test_non_json_policy_is_422(make_client):
    r = make_client(scripted()).put(
        "/tenants/acme/policy", content=b"nope",
        headers={**ADMIN, "content-type": "application/json"},
    )
    assert r.status_code == 422


def test_policy_store_is_replaceable(make_client):
    class RecordingStore(InMemoryPolicyStore):
        def __init__(self):
            super().__init__()
            self.ops = []

        async def get(self, tenant_id):
            self.ops.append(("get", tenant_id))
            return await super().get(tenant_id)

    client = make_client(scripted(ai("ok")))
    client.app.state.policy_store = store = RecordingStore()
    put(client, "acme", policy(blocked_topics=["cats"]))
    assert chat(client, "cats?", tenant="acme").status_code == 403
    assert ("get", "acme") in store.ops


def test_in_memory_store_returns_copies():
    asyncio.run(_check_store_returns_copies())


async def _check_store_returns_copies():
    store = InMemoryPolicyStore()
    stored = await store.put("t", Policy(**policy()))
    stored.blocked_topics.append("leak")
    (await store.get("t")).blocked_topics.append("leak")
    assert (await store.get("t")).blocked_topics == ["weapons", "malware"]
    await store.delete("t")
    await store.delete("t")
    assert await store.get("t") is None


# ------------------------------------------------------------------ tenant resolution


def test_no_header_uses_default_tenant(make_client):
    client = make_client(scripted(ai("ok"), ai("ok")))
    put(client, "default", policy(blocked_topics=["cats"]))
    assert chat(client, "cats").status_code == 403
    assert chat(client, "cats", tenant="").status_code == 403
    assert chat(client, "weapons").status_code == 200


def test_default_tenant_policy_does_not_leak_to_other_tenants(make_client):
    client = make_client(scripted(ai("ok")))
    put(client, "default", policy(blocked_topics=[]))
    assert chat(client, "weapons", tenant="acme").status_code == 403
    assert get(client, "acme").json() == DEFAULT_POLICY


def test_overlong_tenant_header_is_422(make_client):
    model = scripted()
    assert chat(make_client(model), "hi", tenant="t" * 129).status_code == 422
    assert model.calls == 0


def test_env_settings_shape_default_policy(make_client):
    client = make_client(scripted(), blocked_topics=("gambling",), tool_call_limit=2)
    assert get(client, "x").json() == policy(blocked_topics=["gambling"], max_tool_calls=2)


# ------------------------------------------------------------------ /chat applies policy


def test_tenant_blocked_topics(make_client):
    model = scripted(ai("ok"), ai("ok"))
    client = make_client(model)
    put(client, "casino", policy(blocked_topics=["gambling"]))
    r = chat(client, "Tell me about Gambling", tenant="casino")
    assert r.status_code == 403
    assert r.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert chat(client, "weapons history", tenant="casino").status_code == 200
    assert chat(client, "gambling odds", tenant="other").status_code == 200
    assert chat(client, "weapons", tenant="other").status_code == 403
    assert model.calls == 2


def test_empty_blocklist_blocks_nothing(make_client):
    client = make_client(scripted(ai("ok")))
    put(client, "open", policy(blocked_topics=[]))
    assert chat(client, "malware analysis", tenant="open").status_code == 200


def test_redact_pii_false_passes_message_and_reply_unchanged(make_client):
    model = scripted(ai("Mail support@corp.com or call 555-123-4567."))
    client = make_client(model)
    put(client, "raw", policy(redact_pii=False))
    msg = "I'm jane@example.com, +44 20 7946 0958"
    r = chat(client, msg, tenant="raw")
    assert r.json() == {
        "session_id": "s1",
        "reply": "Mail support@corp.com or call 555-123-4567.",
        "guardrails": [],
    }
    assert model.human_texts() == [msg]


def test_redact_pii_false_still_applies_blocklist(make_client):
    model = scripted()
    client = make_client(model)
    put(client, "raw", policy(redact_pii=False))
    assert chat(client, "malware for me@x.com", tenant="raw").status_code == 403
    assert model.calls == 0


def test_redaction_still_on_for_other_tenants(make_client):
    model = scripted(ai("ok bob@x.org"))
    client = make_client(model)
    put(client, "raw", policy(redact_pii=False))
    r = chat(client, "I'm bob@x.org", tenant="acme")
    assert r.json()["reply"] == "ok [REDACTED_EMAIL]"
    assert r.json()["guardrails"] == [PII]
    assert model.human_texts() == ["I'm [REDACTED_EMAIL]"]


def test_tenant_tool_call_limit(make_client, calc_counter):
    model = endless(lambda: ai("", tool_call("calculator", expression="1+1")))
    client = make_client(model)
    put(client, "tight", policy(max_tool_calls=2))
    r = chat(client, "loop", tenant="tight")
    assert r.json()["guardrails"] == [LIMIT]
    assert r.json()["reply"] == LIMIT_REACHED_REPLY.format(limit=2)
    assert len(calc_counter) == 2

    calc_counter.clear()
    r = chat(client, "loop", tenant="other")
    assert r.json()["reply"] == LIMIT_REACHED_REPLY.format(limit=5)
    assert len(calc_counter) == 5


def test_tenant_limit_can_exceed_default(make_client, calc_counter):
    calls = [ai("", tool_call("calculator", expression=f"{i}")) for i in range(7)]
    client = make_client(scripted(*calls, ai("done")))
    put(client, "big", policy(max_tool_calls=7))
    assert chat(client, "go", tenant="big").json()["guardrails"] == []
    assert len(calc_counter) == 7


def test_tenant_limit_covers_subagents(make_client, calc_counter):
    responses = iter(
        [ai("", tool_call("task", description="loop", subagent_type="general-purpose"))]
    )
    model = endless(
        lambda: next(responses, None) or ai("", tool_call("calculator", expression="2+2"))
    )
    client = make_client(model)
    put(client, "t", policy(max_tool_calls=2))
    assert chat(client, "delegate", tenant="t").json()["guardrails"] == [LIMIT]
    assert len(calc_counter) == 1


def test_system_prompt_added_to_instructions(make_client):
    model = scripted(ai("bonjour"), ai("hello"))
    client = make_client(model)
    put(client, "fr", policy(system_prompt="Always answer in French."))

    chat(client, "hi", tenant="fr")
    [system] = system_texts(model)
    assert system.startswith("You are a helpful assistant.")  # base prompt kept
    assert system.endswith("\n\nAlways answer in French.")

    chat(client, "hi", tenant="en")
    assert "French" not in system_texts(model)[1]
    assert system_texts(model)[1].startswith("You are a helpful assistant.")


def test_system_prompt_reaches_subagent(make_client):
    model = scripted(
        ai("", tool_call("task", description="say hi", subagent_type="general-purpose")),
        ai("sub done"),
        ai("main done"),
    )
    client = make_client(model)
    put(client, "fr", policy(system_prompt="Always answer in French."))
    assert chat(client, "go", tenant="fr").json()["reply"] == "main done"
    systems = system_texts(model)
    assert len(systems) == 3
    assert all(s.endswith("Always answer in French.") for s in systems)
    assert len(set(systems)) == 2  # main agent and subagent have different base prompts


def test_policy_changes_apply_to_next_request(make_client):
    model = scripted(ai("one"), ai("two"))
    client = make_client(model)
    put(client, "t", policy(system_prompt="Be terse."))
    chat(client, "a", tenant="t")
    delete(client, "t")
    chat(client, "b", tenant="t")
    assert system_texts(model)[0].endswith("Be terse.")
    assert "Be terse." not in system_texts(model)[1]


def test_concurrent_tenants_keep_their_own_policy(make_client):
    model = endless(lambda: ai("ok"))
    client = make_client(model)
    tenants = [f"t{i}" for i in range(8)]
    for t in tenants:
        put(client, t, policy(system_prompt=f"PROMPT-{t}"))

    async def send_all():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            return await asyncio.gather(
                *(
                    ac.post("/chat", json={"session_id": t, "message": f"MSG-{t}"},
                            headers={"X-Tenant-ID": t})
                    for t in tenants * 3
                )
            )

    responses = asyncio.run(send_all())
    assert all(r.status_code == 200 for r in responses)
    assert len(model.received) == len(tenants) * 3
    for msgs in model.received:
        human = next(m.text for m in msgs if m.type == "human")
        system = next(m.text for m in msgs if m.type == "system")
        assert system.endswith(f"PROMPT-{human.removeprefix('MSG-')}")
