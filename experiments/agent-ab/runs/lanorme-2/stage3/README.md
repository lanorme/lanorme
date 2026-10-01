# Guarded agent

A FastAPI service that serves a conversational agent built with deepagents
(`create_deep_agent`), wrapped in three guardrails: PII redaction, a topic
blocklist and a per-turn tool-call limit. Each tenant can have its own
guardrail policy.

## Run the service

```sh
uv sync
export ANTHROPIC_API_KEY=...   # or the key for whichever provider AGENT_MODEL names
uv run uvicorn app.main:create_app --factory
```

Configuration comes from environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_MODEL` | `anthropic:claude-sonnet-5-5` | Model name passed to `init_chat_model` |
| `BLOCKED_TOPICS` | `weapons,malware` | Comma-separated topics; matched case-insensitively as whole words |
| `MAX_TOOL_CALLS` | `5` | Most tool calls one turn may make |
| `ADMIN_API_KEY` | unset | Key the tenant policy endpoints require; unset or empty refuses every request |

`BLOCKED_TOPICS` and `MAX_TOOL_CALLS` form the default policy (with PII
redaction on and no extra system prompt), used by every tenant without one.

## Endpoints

- `GET /health` returns `{"status": "ok"}`.
- `POST /chat` takes `{"session_id": str, "message": str}` and returns
  `{"session_id", "reply", "guardrails": [{"name", "action"}]}`. A blocked
  topic returns 403 `{"error": "blocked", "guardrail": "topic_blocklist"}`;
  a model failure returns 502 `{"error": "agent_unavailable"}`.
  The `X-Tenant-ID` header names the tenant (`"default"` when absent), and the
  turn runs under that tenant's policy.
- `POST /chat/stream` takes the same body and headers and returns
  `text/event-stream`: one `data: {"type": "token", "content": str}` event per
  chunk of the reply, then `data: {"type": "done", "session_id", "guardrails"}`.
  The tokens join to exactly the reply `/chat` would return. Output redaction
  holds back text until no email or phone number can still be forming, so PII
  split across chunks is still caught. A blocked topic gets the same 403 JSON
  as `/chat`, and a model that fails before any token gets the 502. If it fails
  later, the stream ends with `{"type": "error", "error": "agent_unavailable"}`
  instead of `done`.
- `GET /sessions/{session_id}/messages` returns `{"session_id", "messages":
  [{"role": "user" | "assistant", "content"}]}` in order, as stored (redacted
  under the tenant's policy); `DELETE /sessions/{session_id}` returns 204 and
  forgets the conversation. Both take the tenant from `X-Tenant-ID` and return
  404 for an unknown session.
- `GET`, `PUT` and `DELETE /tenants/{tenant_id}/policy` read, replace and reset
  a tenant's policy `{"blocked_topics": [str], "redact_pii": bool,
  "max_tool_calls": int, "system_prompt": str | null}`. They need an
  `X-Admin-Key` header equal to `ADMIN_API_KEY` (401 otherwise). `PUT` takes
  the full policy (422 when invalid); `DELETE` returns 204 and the tenant falls
  back to the default. A tenant's `system_prompt` is appended to the agent's
  own instructions.

### Conversation memory

Turns sent with the same `session_id` (under the same tenant) continue one
conversation: the model sees the earlier turns. A turn is stored once its
reply is complete, as the redacted user message and the reply the user was
sent. Turns refused by the blocklist, turns that fail (502) and streams the
client abandons are not stored. History is redacted again under the tenant's
current policy before the model sees it, so turning redaction on also covers
older turns.

When a model call writes text and also calls a tool, that text is part of
the reply. Texts from separate model calls are joined by a blank line. The
same rule applies to `/chat` and `/chat/stream`, because both run through one
streaming code path.

Policies and conversations are held in memory and lost on restart. Storage
sits behind the `PolicyRepository` and `ConversationRepository` ports, so
database adapters can replace it. Conversations are not trimmed or expired
yet.

## Run the tests and the linter

```sh
uv run pytest
uvx lanorme@0.21.0 check .
```

The tests drive the real deep agent with a scripted fake chat model, so they
need no API key and make no network calls.

## Layout

- `app/domain`: guardrail rules (PII redaction, including the
  `StreamingRedactor` for chunked text, and the topic blocklist), the
  `GuardrailPolicy`, conversation messages and errors.
- `app/application`: the `ChatAgent`, `PolicyRepository` and
  `ConversationRepository` ports, the `PolicyService` that resolves a tenant's
  effective policy, the `ChatService` that applies it around one streamed
  turn, and the `ConversationService` that reads and forgets sessions.
- `app/infrastructure`: settings, the agent's own tools (calculator, clock),
  the deepagents adapter (which enforces the tool-call limit and caches one
  compiled graph per limit and system prompt, and streams the agent's own
  model output) and the in-memory policy and conversation stores.
- `app/api`: HTTP routes (`routes.py` for chat, `sessions.py` for
  conversations, `tenants.py` for policies) and schemas.
- `app/main.py`: `create_app`, the composition root.
