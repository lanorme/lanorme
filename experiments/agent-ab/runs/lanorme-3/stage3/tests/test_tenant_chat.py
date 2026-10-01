"""POST /chat applies the calling tenant's policy, named by the X-Tenant-ID header."""

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from app.agent import SYSTEM_PROMPT
from app.main import create_app
from tests.fakes import ScriptedChatModel, make_policy, put_policy, repeat_calculator_calls, find_system_text

ACME = {"X-Tenant-ID": "acme"}
BLOCKED_BODY = {"error": "blocked", "guardrail": "topic_blocklist"}
PII_MESSAGE = "I am jane@example.com, ring me on (555) 123-4567"


def build_client(model: ScriptedChatModel) -> TestClient:
    return TestClient(create_app(model))


def send_chat(client: TestClient, message: str, headers: dict[str, str] | None = None) -> dict[str, object]:
    response = client.post("/chat", json={"session_id": "s1", "message": message}, headers=headers or {})
    return {"status": response.status_code} | response.json()


def test_tenant_topics_replace_the_default_ones(admin_key: str) -> None:
    # Given
    model = ScriptedChatModel(messages=iter(["About malware."]))
    client = build_client(model)
    put_policy(client=client, tenant_id="acme", policy=make_policy(blocked_topics=["gambling"]))

    # When
    blocked = client.post("/chat", json={"session_id": "s1", "message": "Best gambling odds?"}, headers=ACME)
    allowed = client.post("/chat", json={"session_id": "s1", "message": "Tell me about malware"}, headers=ACME)

    # Then
    assert blocked.status_code == 403
    assert blocked.json() == BLOCKED_BODY
    assert allowed.status_code == 200
    assert len(model.received) == 1


def test_other_tenants_keep_the_default_blocklist(admin_key: str, replying_model: ScriptedChatModel) -> None:
    # Given
    client = build_client(replying_model)
    put_policy(client=client, tenant_id="acme", policy=make_policy(blocked_topics=[]))

    # When
    outcomes = [
        send_chat(client, "malware?", {"X-Tenant-ID": "globex"})["status"],
        send_chat(client, "malware?")["status"],
        send_chat(client, "malware?", {"X-Tenant-ID": "default"})["status"],
    ]

    # Then
    assert outcomes == [403, 403, 403]
    assert replying_model.received == []


def test_missing_header_means_the_default_tenant(admin_key: str, replying_model: ScriptedChatModel) -> None:
    # Given
    client = build_client(replying_model)
    put_policy(client=client, tenant_id="default", policy=make_policy(blocked_topics=["cats"]))

    # When
    without_header = send_chat(client, "I love cats")
    empty_header = send_chat(client, "I love cats", {"X-Tenant-ID": ""})

    # Then
    assert without_header == {"status": 403} | BLOCKED_BODY
    assert empty_header == {"status": 403} | BLOCKED_BODY


def test_redaction_can_be_switched_off(admin_key: str) -> None:
    # Given
    leaky_reply = "Contact support@example.org or +44 20 7946 0958."
    model = ScriptedChatModel(messages=iter([AIMessage(content=leaky_reply)]))
    client = build_client(model)
    put_policy(client=client, tenant_id="acme", policy=make_policy(redact_pii=False))

    # When
    body = send_chat(client, PII_MESSAGE, ACME)

    # Then
    assert model.last_user_text == PII_MESSAGE
    assert body["reply"] == leaky_reply
    assert body["guardrails"] == []


def test_redaction_still_applies_to_tenants_that_keep_it(admin_key: str, replying_model: ScriptedChatModel) -> None:
    # Given
    client = build_client(replying_model)
    put_policy(client=client, tenant_id="other", policy=make_policy(redact_pii=False))

    # When
    body = send_chat(client, PII_MESSAGE, ACME)

    # Then
    assert replying_model.last_user_text == "I am [REDACTED_EMAIL], ring me on [REDACTED_PHONE]"
    assert body["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]


@pytest.mark.parametrize("limit", [1, 3, 8])
def test_tool_call_limit_follows_the_tenant(admin_key: str, limit: int) -> None:
    # Given
    model = ScriptedChatModel(messages=repeat_calculator_calls())
    client = build_client(model)
    put_policy(client=client, tenant_id="acme", policy=make_policy(max_tool_calls=limit))

    # When
    body = send_chat(client, "Count forever", ACME)

    # Then
    assert body["status"] == 200
    assert body["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]
    assert f"limit of {limit} tool calls" in str(body["reply"])
    assert sum(isinstance(message, ToolMessage) for message in model.received[-1]) == limit


def test_tenant_limits_do_not_leak_between_tenants(admin_key: str) -> None:
    # Given
    model = ScriptedChatModel(messages=repeat_calculator_calls())
    client = build_client(model)
    put_policy(client=client, tenant_id="acme", policy=make_policy(max_tool_calls=1))

    # When
    acme = send_chat(client, "Count forever", ACME)
    default = send_chat(client, "Count forever")

    # Then
    assert "limit of 1 tool calls" in str(acme["reply"])
    assert "limit of 5 tool calls" in str(default["reply"])


def test_system_prompt_is_added_to_the_agent_instructions(admin_key: str) -> None:
    # Given
    model = ScriptedChatModel(messages=iter(["Bonjour.", "Hello."]))
    client = build_client(model)
    put_policy(client=client, tenant_id="acme", policy=make_policy(system_prompt="Always answer in French."))

    # When
    send_chat(client, "Hi", ACME)
    acme_instructions = find_system_text(model)
    send_chat(client, "Hi")
    default_instructions = find_system_text(model)

    # Then
    assert SYSTEM_PROMPT in acme_instructions
    assert "Always answer in French." in acme_instructions
    assert SYSTEM_PROMPT in default_instructions
    assert "French" not in default_instructions


def test_policy_changes_apply_to_the_next_turn(admin_key: str) -> None:
    # Given
    model = ScriptedChatModel(messages=iter(["One.", "Two.", "Three."]))
    client = build_client(model)
    put_policy(client=client, tenant_id="acme", policy=make_policy(system_prompt="Be terse."))
    send_chat(client, "Hi", ACME)

    # When
    put_policy(client=client, tenant_id="acme", policy=make_policy(system_prompt="Be verbose."))
    send_chat(client, "Hi", ACME)
    replaced = find_system_text(model)
    client.delete("/tenants/acme/policy", headers={"X-Admin-Key": admin_key})
    send_chat(client, "Hi", ACME)
    deleted = find_system_text(model)

    # Then
    assert "Be verbose." in replaced
    assert "Be terse." not in replaced
    assert "Be verbose." not in deleted


def test_tenant_policy_overrides_environment_defaults(
    monkeypatch: pytest.MonkeyPatch, admin_key: str, replying_model: ScriptedChatModel
) -> None:
    # Given
    monkeypatch.setenv("BLOCKED_TOPICS", "gambling")
    client = build_client(replying_model)
    put_policy(client=client, tenant_id="acme", policy=make_policy(blocked_topics=["cats"]))

    # When
    statuses = [send_chat(client, "gambling", ACME)["status"], send_chat(client, "gambling")["status"]]

    # Then
    assert statuses == [200, 403]
