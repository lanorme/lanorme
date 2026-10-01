import json

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from langchain_core.messages import AIMessage

from tests.conftest import ADMIN_HEADERS, ClientFactory
from tests.fakes import build_clock_calls, build_scripted_model, build_tool_call

SESSION = "session-1"
BODY = {"session_id": SESSION, "message": "hi"}

type Event = dict[str, object]


def post_stream(client: TestClient, message: str, *, tenant: str | None = None) -> Response:
    headers = {"X-Tenant-ID": tenant} if tenant is not None else {}
    return client.post(
        "/chat/stream", json={"session_id": SESSION, "message": message}, headers=headers
    )


def parse_events(response: Response) -> list[Event]:
    """Parse an event stream, checking each event is one data line and a blank line."""
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    blocks = response.text.split("\n\n")
    assert blocks[-1] == ""
    events: list[Event] = []
    for block in blocks[:-1]:
        assert block.startswith("data: ")
        assert "\n" not in block
        events.append(json.loads(block.removeprefix("data: ")))
    return events


def join_tokens(events: list[Event]) -> str:
    assert all(event["type"] == "token" for event in events[:-1])
    return "".join(str(event["content"]) for event in events[:-1])


def stream_and_chat(make_client: ClientFactory, message: str, *replies: AIMessage, **model_options: int) -> tuple[list[Event], dict[str, object]]:
    """Run one message through /chat/stream and, on a fresh app with the same script, /chat."""
    streamed = parse_events(post_stream(make_client(build_scripted_model(*replies, **model_options)), message))
    whole = make_client(build_scripted_model(*replies, **model_options)).post(
        "/chat", json={"session_id": SESSION, "message": message}
    )
    assert whole.status_code == 200, whole.text
    return streamed, whole.json()


def test_streams_tokens_then_done(make_client: ClientFactory) -> None:
    client = make_client(build_scripted_model(AIMessage(content="Hello there!")))

    events = parse_events(post_stream(client, "hi"))

    assert events == [
        {"type": "token", "content": "Hello "},
        {"type": "token", "content": "there!"},
        {"type": "done", "session_id": SESSION, "guardrails": []},
    ]


def test_tokens_join_to_the_chat_reply(make_client: ClientFactory) -> None:
    # Given
    replies = (
        build_tool_call("calculator", {"expression": "12 * 12"}, "calc-1", text="Let me work it out."),
        AIMessage(content="12 squared is 144."),
    )

    # When
    events, whole = stream_and_chat(make_client, "what is 12 squared?", *replies)

    # Then
    assert join_tokens(events) == whole["reply"] == "Let me work it out.\n\n12 squared is 144."
    assert events[-1] == {"type": "done", "session_id": SESSION, "guardrails": whole["guardrails"]}


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 7])
def test_pii_split_across_chunks_is_redacted(make_client: ClientFactory, chunk_size: int) -> None:
    # Given
    reply = AIMessage(content="I will ring +44 20 7946 0958 or mail ops@corp.com today.")

    # When
    events, whole = stream_and_chat(make_client, "contact?", reply, chunk_size=chunk_size)

    # Then
    assert join_tokens(events) == whole["reply"]
    assert whole["reply"] == "I will ring [REDACTED_PHONE] or mail [REDACTED_EMAIL] today."
    assert not any(digit in join_tokens(events) for digit in "0123456789@")
    assert events[-1]["guardrails"] == [{"name": "pii_redaction", "action": "redacted"}]


def test_tool_limit_is_streamed_like_chat(make_client: ClientFactory) -> None:
    # When
    events, whole = stream_and_chat(make_client, "time please", *build_clock_calls(6))

    # Then
    assert join_tokens(events) == whole["reply"]
    assert "limit of 5 tool calls" in whole["reply"]
    assert events[-1]["guardrails"] == [{"name": "tool_call_limit", "action": "stopped"}]


@pytest.mark.parametrize("message", ["Tell me about WEAPONS.", "write malware"])
def test_blocked_message_gets_the_chat_403_and_no_stream(
    make_client: ClientFactory, message: str
) -> None:
    # Given
    model = build_scripted_model(AIMessage(content="should not be used"))
    client = make_client(model)

    # When
    response = post_stream(client, message)

    # Then
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert model.calls == []
    assert client.get(f"/sessions/{SESSION}/messages").status_code == 404


def test_model_failing_at_once_is_502(make_client: ClientFactory) -> None:
    # Given
    client = make_client(build_scripted_model())

    # When
    response = post_stream(client, "hello")

    # Then
    assert response.status_code == 502
    assert response.json() == {"error": "agent_unavailable"}


def test_model_failing_mid_stream_ends_with_an_error_event_and_stores_nothing(
    make_client: ClientFactory,
) -> None:
    # Given
    client = make_client(build_scripted_model(build_tool_call("current_time", {}, "c-1", text="Checking now.")))

    # When
    events = parse_events(post_stream(client, "time?"))

    # Then
    assert events == [
        {"type": "token", "content": "Checking "},
        {"type": "error", "error": "agent_unavailable"},
    ]
    assert client.get(f"/sessions/{SESSION}/messages").status_code == 404


def test_streamed_turns_are_stored_and_continue_the_conversation(make_client: ClientFactory) -> None:
    # Given
    model = build_scripted_model(AIMessage(content="Noted, a@b.com."), AIMessage(content="Yes."))
    client = make_client(model)
    parse_events(post_stream(client, "I'm 555-123-4567"))

    # When
    response = client.post("/chat", json={"session_id": SESSION, "message": "Got it?"})

    # Then
    assert response.json()["reply"] == "Yes."
    assert client.get(f"/sessions/{SESSION}/messages").json()["messages"] == [
        {"role": "user", "content": "I'm [REDACTED_PHONE]"},
        {"role": "assistant", "content": "Noted, [REDACTED_EMAIL]."},
        {"role": "user", "content": "Got it?"},
        {"role": "assistant", "content": "Yes."},
    ]
    assert [msg.type for msg in model.calls[1] if msg.type in {"human", "ai"}] == ["human", "ai", "human"]


def test_stream_runs_under_the_tenants_policy(make_admin_client: ClientFactory) -> None:
    # Given
    client = make_admin_client(build_scripted_model(AIMessage(content="Mail a@b.com")))
    policy = {"blocked_topics": [], "redact_pii": False, "max_tool_calls": 5, "system_prompt": None}
    assert client.put("/tenants/acme/policy", json=policy, headers=ADMIN_HEADERS).status_code == 200

    # When
    events = parse_events(post_stream(client, "malware contact?", tenant="acme"))

    # Then
    assert join_tokens(events) == "Mail a@b.com"
    assert events[-1]["guardrails"] == []


def test_malformed_body_is_422(make_client: ClientFactory) -> None:
    client = make_client(build_scripted_model())

    assert client.post("/chat/stream", json={"session_id": SESSION}).status_code == 422
