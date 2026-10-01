import json
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from tests.conftest import ADMIN
from tests.fakes import (
    ModelDownError,
    ScriptedChatModel,
    calling,
    endless_calculator_calls,
    failing_after,
    script,
    tool_call,
)

ClientFor = Callable[[ScriptedChatModel], TestClient]
REDACTED = {"name": "pii_redaction", "action": "redacted"}
STOPPED = {"name": "tool_call_limit", "action": "stopped"}
TURN = {"session_id": "s-1", "message": "hello"}


def events_of(body: str) -> list[dict]:
    """Parse a text/event-stream body, insisting on one ``data:`` line per event."""
    assert body.endswith("\n\n"), body
    events = []
    for block in body.removesuffix("\n\n").split("\n\n"):
        assert block.startswith("data: "), block
        assert "\n" not in block, block
        events.append(json.loads(block.removeprefix("data: ")))
    return events


def stream(client: TestClient, payload: dict = TURN, headers: dict | None = None) -> list[dict]:
    response = client.post("/chat/stream", json=payload, headers=headers or {})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return events_of(response.text)


def streamed_reply(events: list[dict]) -> str:
    assert all(event["type"] == "token" for event in events[:-1])
    return "".join(event["content"] for event in events[:-1])


def test_tokens_then_one_done_event(client_for: ClientFor) -> None:
    # Given
    model = script("Hello there, how can I help?", chunk_size=4)

    # When
    events = stream(client_for(model))

    # Then
    assert len(events) > 3
    assert all(set(event) == {"type", "content"} for event in events[:-1])
    assert streamed_reply(events) == "Hello there, how can I help?"
    assert events[-1] == {"type": "done", "session_id": "s-1", "guardrails": []}


def preamble_then_answer() -> list[AIMessage | str]:
    return [
        AIMessage(
            content="Let me work that out.",
            tool_calls=[tool_call("calculator", {"expression": "6 * 7"}, "c1")],
        ),
        "The answer is 42.",
    ]


def delegation() -> list[AIMessage | str]:
    task = {"description": "add", "subagent_type": "general-purpose"}
    return [
        AIMessage(content="Asking a helper.", tool_calls=[tool_call("task", task, "d1")]),
        calling(tool_call("calculator", {"expression": "1 + 1"}, "c1")),
        "SUBAGENT PRIVATE NOTE",
        "It is 2.",
    ]


SCRIPTS = {
    "plain": lambda: ["Hello there!"],
    "multiline": lambda: ["Line one.\n\nLine two\twith a tab."],
    "empty": lambda: [""],
    "pii": lambda: ["Write to jane.doe@example.com or call +44 20 7946 0958 (555) 123-4567."],
    "pii at the end": lambda: ["Call me on 555-123-4567"],
    "tool then answer": lambda: [calling(tool_call("current_time", {}, "t1")), "It is noon."],
    "preamble": preamble_then_answer,
    "subagent": delegation,
    "stopped": lambda: [*(calling(tool_call("calculator", {"expression": "1"}, f"c{i}"))
                          for i in range(6))],
    "stopped after preamble": lambda: [
        AIMessage(content="Mail ann@corp.io first.", tool_calls=[
            tool_call("calculator", {"expression": "1"}, f"c{i}") for i in range(6)
        ])
    ],
}


@pytest.mark.parametrize("chunk_size", [1, 2, 5, 1000])
@pytest.mark.parametrize("name", list(SCRIPTS))
def test_stream_matches_chat(client_for: ClientFor, name: str, chunk_size: int) -> None:
    # Given two services with the same scripted model
    def model() -> ScriptedChatModel:
        return script(*SCRIPTS[name](), chunk_size=chunk_size)

    message = {"session_id": "s-1", "message": "Mail me at bob@example.org"}

    # When
    chat = client_for(model()).post("/chat", json=message).json()
    events = stream(client_for(model()), message)

    # Then
    assert streamed_reply(events) == chat["reply"]
    assert events[-1] == {"type": "done", "session_id": "s-1", "guardrails": chat["guardrails"]}


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7])
def test_pii_split_across_chunks_is_redacted(client_for: ClientFor, chunk_size: int) -> None:
    # Given
    reply = "Reach jane.doe@example.com or +1 (555) 123-4567, thanks."

    # When
    events = stream(client_for(script(reply, chunk_size=chunk_size)))

    # Then
    tokens = [event["content"] for event in events[:-1]]
    assert "".join(tokens) == "Reach [REDACTED_EMAIL] or [REDACTED_PHONE], thanks."
    assert not any("@" in token or "555" in token or "4567" in token for token in tokens)
    assert events[-1]["guardrails"] == [REDACTED]


def test_text_is_streamed_without_waiting_for_the_whole_reply(client_for: ClientFor) -> None:
    events = stream(client_for(script("one two three four five six", chunk_size=4)))

    assert len(events) - 1 >= 5


def test_subagent_text_is_not_streamed(client_for: ClientFor) -> None:
    events = stream(client_for(script(*delegation())))

    assert streamed_reply(events) == "Asking a helper.\n\nIt is 2."


def test_tool_limit_ends_the_stream_with_the_notice(client_for: ClientFor) -> None:
    # Given
    model = ScriptedChatModel(messages=endless_calculator_calls())

    # When
    events = stream(client_for(model))

    # Then
    assert "at most 5 tool calls" in streamed_reply(events)
    assert events[-1]["guardrails"] == [STOPPED]
    assert model.calls == 6


def test_redaction_off_streams_pii_unchanged(admin_client_for: ClientFor) -> None:
    # Given
    client = admin_client_for(script("Mail help@corp.io", chunk_size=2))
    policy = {"blocked_topics": [], "redact_pii": False, "max_tool_calls": 1,
              "system_prompt": None}
    client.put("/tenants/acme/policy", json=policy, headers=ADMIN)

    # When
    events = stream(client, headers={"X-Tenant-ID": "acme"})

    # Then
    assert streamed_reply(events) == "Mail help@corp.io"
    assert events[-1]["guardrails"] == []


def test_a_blocked_message_is_a_403_with_no_stream(client_for: ClientFor) -> None:
    # Given
    model = script("never")
    client = client_for(model)

    # When
    response = client.post("/chat/stream", json={"session_id": "s-1", "message": "Malware?"})

    # Then
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"error": "blocked", "guardrail": "topic_blocklist"}
    assert model.calls == 0
    assert client.get("/sessions/s-1/messages").status_code == 404


def test_a_malformed_body_is_422(client_for: ClientFor) -> None:
    response = client_for(script()).post("/chat/stream", json={"message": "hi"})

    assert response.status_code == 422


def test_streamed_turns_are_stored_and_remembered(client_for: ClientFor) -> None:
    # Given
    model = script("I'll remember jane@corp.io.", "Streamed again.", "Yes.")
    client = client_for(model)

    # When
    stream(client, {"session_id": "s-1", "message": "Remember jane@corp.io"})
    stream(client, {"session_id": "s-1", "message": "Again"})
    client.post("/chat", json={"session_id": "s-1", "message": "Did you?"})

    # Then
    stored = client.get("/sessions/s-1/messages").json()["messages"]
    assert [m["content"] for m in stored] == [
        "Remember [REDACTED_EMAIL]",
        "I'll remember [REDACTED_EMAIL].",
        "Again",
        "Streamed again.",
        "Did you?",
        "Yes.",
    ]
    assert [m.text for m in model.prompts[2] if m.type in {"human", "ai"}] == [
        m["content"] for m in stored[:5]
    ]


def test_a_stream_whose_model_fails_stores_nothing(client_for: ClientFor) -> None:
    # Given
    client = client_for(failing_after())

    # When
    with pytest.raises(ModelDownError):
        client.post("/chat/stream", json=TURN)

    # Then
    assert client.get("/sessions/s-1/messages").status_code == 404
