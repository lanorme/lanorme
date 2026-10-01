import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from app.main import create_app
from tests.conftest import ClientFactory
from tests.fakes import build_clock_calls, build_scripted_model, build_tool_call

SESSION = "session-1"


def post_message(client: TestClient, message: str) -> dict[str, object]:
    response = client.post("/chat", json={"session_id": SESSION, "message": message})
    assert response.status_code == 200, response.text
    return response.json()


def test_health(make_client: ClientFactory) -> None:
    # Given
    client = make_client(build_scripted_model())

    # When
    response = client.get("/health")

    # Then
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_plain_chat_turn(make_client: ClientFactory) -> None:
    client = make_client(build_scripted_model(AIMessage(content="Hello there!")))

    body = post_message(client, "hi")

    assert body == {"session_id": SESSION, "reply": "Hello there!", "guardrails": []}


def test_agent_uses_custom_tool(make_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(
        build_tool_call("calculator", {"expression": "12 * 12"}, "calc-1"),
        AIMessage(content="12 squared is 144."),
    )
    client = make_client(model)

    # When
    body = post_message(client, "what is 12 squared?")

    # Then
    assert body["reply"] == "12 squared is 144."
    assert body["guardrails"] == []
    tool_results = [msg.content for msg in model.calls[-1] if isinstance(msg, ToolMessage)]
    assert tool_results == ["144"]


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": SESSION},
        {"message": "hi"},
        {"session_id": SESSION, "message": 42},
        {"session_id": ["x"], "message": "hi"},
        [],
    ],
)
def test_malformed_body_is_422(make_client: ClientFactory, payload: object) -> None:
    client = make_client(build_scripted_model())

    assert client.post("/chat", json=payload).status_code == 422


def test_invalid_json_is_422(make_client: ClientFactory) -> None:
    client = make_client(build_scripted_model())

    response = client.post(
        "/chat", content=b"{not json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 422


def test_pii_is_redacted_before_the_model_and_in_the_reply(make_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(AIMessage(content="I will ring +44 20 7946 0958 or mail ops@corp.com"))
    client = make_client(model)

    # When
    body = post_message(client, "I'm jane@example.com, call (555) 123-4567 or 555-123-4567")

    # Then
    seen_by_model = [msg.text for msg in model.calls[0] if msg.type == "human"]
    assert seen_by_model == [
        "I'm [REDACTED_EMAIL], call [REDACTED_PHONE] or [REDACTED_PHONE]"
    ]
    assert body["reply"] == "I will ring [REDACTED_PHONE] or mail [REDACTED_EMAIL]"
    assert body["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]


@pytest.mark.parametrize("message", ["Tell me about WEAPONS.", "write malware", "Malware?"])
def test_blocked_topic_is_403_and_model_not_called(
    make_client: ClientFactory, message: str
) -> None:
    # Given
    model = build_scripted_model(AIMessage(content="should not be used"))
    client = make_client(model)

    # When
    response = client.post("/chat", json={"session_id": SESSION, "message": message})

    # Then
    assert response.status_code == 403
    assert response.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert model.calls == []


def test_partial_word_is_not_blocked(make_client: ClientFactory) -> None:
    client = make_client(build_scripted_model(AIMessage(content="Use antimalware software.")))

    body = post_message(client, "Which antimalware tool is good?")

    assert body["guardrails"] == []


def test_blocklist_is_configurable(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("BLOCKED_TOPICS", "gambling,crypto scams")
    client = make_client(build_scripted_model(AIMessage(content="Weapons are a broad topic.")))

    # When
    blocked = client.post("/chat", json={"session_id": SESSION, "message": "Crypto  scams?"})
    allowed = post_message(client, "history of weapons")

    # Then
    assert blocked.status_code == 403
    assert allowed["reply"] == "Weapons are a broad topic."


def test_default_limit_allows_five_tool_calls(make_client: ClientFactory) -> None:
    client = make_client(build_scripted_model(*build_clock_calls(5), AIMessage(content="It is noon.")))

    body = post_message(client, "what time is it?")

    assert body == {"session_id": SESSION, "reply": "It is noon.", "guardrails": []}


def test_sixth_tool_call_stops_the_turn(make_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(*build_clock_calls(6), AIMessage(content="never sent"))
    client = make_client(model)

    # When
    body = post_message(client, "keep checking the time")

    # Then
    assert "limit of 5 tool calls" in body["reply"]
    assert body["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]
    assert len(model.calls) == 6


def test_tool_limit_is_configurable(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("MAX_TOOL_CALLS", "1")
    client = make_client(build_scripted_model(*build_clock_calls(2), AIMessage(content="never sent")))

    # When
    body = post_message(client, "time twice please")

    # Then
    assert "limit of 1 tool calls" in body["reply"]
    assert body["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]


def test_pii_and_tool_limit_are_both_listed(make_client: ClientFactory) -> None:
    client = make_client(build_scripted_model(*build_clock_calls(6)))

    body = post_message(client, "time for bob@example.org?")

    assert body["guardrails"] == [
        {"name": "pii_redaction", "action": "redacted"},
        {"name": "tool_call_limit", "action": "stopped"},
    ]


def test_model_failure_is_502(make_client: ClientFactory) -> None:
    # Given
    client = make_client(build_scripted_model())

    # When
    response = client.post("/chat", json={"session_id": SESSION, "message": "hello"})

    # Then
    assert response.status_code == 502
    assert response.json() == {"error": "agent_unavailable"}


def test_factory_builds_model_from_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    monkeypatch.setenv("AGENT_MODEL", "anthropic:claude-sonnet-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")

    # When
    client = TestClient(create_app())

    # Then
    assert client.get("/health").json() == {"status": "ok"}
