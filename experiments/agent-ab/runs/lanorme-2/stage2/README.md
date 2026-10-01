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
- `GET`, `PUT` and `DELETE /tenants/{tenant_id}/policy` read, replace and reset
  a tenant's policy `{"blocked_topics": [str], "redact_pii": bool,
  "max_tool_calls": int, "system_prompt": str | null}`. They need an
  `X-Admin-Key` header equal to `ADMIN_API_KEY` (401 otherwise). `PUT` takes
  the full policy (422 when invalid); `DELETE` returns 204 and the tenant falls
  back to the default. A tenant's `system_prompt` is appended to the agent's
  own instructions.

Each request is independent: there is no conversation memory yet. Policies
are held in memory and lost on restart; storage sits behind the
`PolicyRepository` port, so a database adapter can replace it.

## Run the tests and the linter

```sh
uv run pytest
uvx lanorme@0.21.0 check .
```

The tests drive the real deep agent with a scripted fake chat model, so they
need no API key and make no network calls.

## Layout

- `app/domain`: guardrail rules (PII redaction, topic blocklist), the
  `GuardrailPolicy` and errors.
- `app/application`: the `ChatAgent` and `PolicyRepository` ports, the
  `PolicyService` that resolves a tenant's effective policy and the
  `ChatService` that applies it around one turn.
- `app/infrastructure`: settings, the agent's own tools (calculator, clock),
  the deepagents adapter (which enforces the tool-call limit and caches one
  compiled graph per limit and system prompt) and the in-memory policy store.
- `app/api`: HTTP routes (`routes.py` for chat, `tenants.py` for policies) and
  schemas.
- `app/main.py`: `create_app`, the composition root.
