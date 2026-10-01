"""Request and parsing helpers shared by the stage suites."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import TypedDict, cast

from fastapi.testclient import TestClient
from httpx import Response

ADMIN_KEY = "acceptance-admin-key"
BLOCKED_BODY = {"error": "blocked", "guardrail": "topic_blocklist"}
DEFAULT_TOPICS = ("weapons", "malware")
DEFAULT_POLICY = {
    "blocked_topics": list(DEFAULT_TOPICS),
    "redact_pii": True,
    "max_tool_calls": 5,
    "system_prompt": None,
}
PII = {"name": "pii_redaction", "action": "redacted"}
TOOL_LIMIT = {"name": "tool_call_limit", "action": "stopped"}
# Words a reply saying "the tool-call limit was reached" can be expected to use.
LIMIT_WORDS = re.compile(r"limit|maximum|\bmax\b|too many", re.IGNORECASE)


class Guardrail(TypedDict):
    """One entry of a `guardrails` list."""

    name: str
    action: str


class StreamEvent(TypedDict, total=False):
    """One `data:` payload of the chat stream: a token or the final done."""

    type: str
    content: str
    session_id: str
    guardrails: list[Guardrail]


def build_id(prefix: str) -> str:
    """Return an id unique to this test, so state never leaks between tests."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def build_headers(*, tenant: str | None = None, admin: str | None = None) -> dict[str, str]:
    """Return request headers naming a tenant and an admin key, when given."""
    headers = {}
    if tenant is not None:
        headers["X-Tenant-ID"] = tenant
    if admin is not None:
        headers["X-Admin-Key"] = admin
    return headers


def post_chat(
    client: TestClient,
    *,
    message: str,
    session: str | None = None,
    tenant: str | None = None,
) -> Response:
    """POST one chat turn; a fresh session id is used unless one is given."""
    body = {"session_id": session or build_id("s"), "message": message}
    return client.post("/chat", json=body, headers=build_headers(tenant=tenant))


def post_stream(
    client: TestClient,
    *,
    message: str,
    session: str | None = None,
    tenant: str | None = None,
) -> Response:
    """POST one streamed chat turn and return the full response."""
    body = {"session_id": session or build_id("s"), "message": message}
    return client.post("/chat/stream", json=body, headers=build_headers(tenant=tenant))


def put_policy(
    client: TestClient,
    *,
    tenant: str,
    blocked_topics: list[str] | None = None,
    redact_pii: bool = True,
    max_tool_calls: int = 5,
    system_prompt: str | None = None,
) -> Response:
    """Store a full policy for a tenant; fields not given take the default."""
    policy = {
        "blocked_topics": list(DEFAULT_TOPICS) if blocked_topics is None else blocked_topics,
        "redact_pii": redact_pii,
        "max_tool_calls": max_tool_calls,
        "system_prompt": system_prompt,
    }
    return client.put(
        f"/tenants/{tenant}/policy",
        json=policy,
        headers=build_headers(admin=ADMIN_KEY),
    )


def find_guardrails(body: Mapping[str, object]) -> list[Guardrail]:
    """Return the guardrail entries of a chat or done body, as name/action pairs."""
    entries = cast("list[Guardrail]", body["guardrails"])
    return [{"name": entry["name"], "action": entry["action"]} for entry in entries]


def parse_sse(text: str) -> list[StreamEvent]:
    """Parse a `text/event-stream` body into its JSON `data:` payloads.

    Lines are split on LF or CRLF (both are valid SSE); comment lines (`:`)
    and blocks with no data line, such as keep-alive pings, are skipped. Each
    remaining block must carry exactly one `data:` line.
    """
    events = []
    for block in text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n"):
        data_lines = [line for line in block.split("\n") if line.startswith("data:")]
        if not data_lines:
            continue
        assert len(data_lines) == 1, f"an event carries more than one data line: {block!r}"
        events.append(cast("StreamEvent", json.loads(data_lines[0].removeprefix("data:").strip())))
    return events


def join_tokens(events: list[StreamEvent]) -> str:
    """Concatenate the content of every token event."""
    return "".join(event["content"] for event in events if event.get("type") == "token")
