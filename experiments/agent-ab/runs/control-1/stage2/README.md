# Guarded deep agent

A FastAPI service that serves a LangChain `deepagents` agent (with `calculator`
and `current_time` tools) behind three guardrails: PII redaction, a topic
blocklist and a per-turn tool-call limit. Each tenant can have its own
guardrail policy.

## Run

```sh
uv sync
export ANTHROPIC_API_KEY=...            # or the key for whichever provider you configure
export ADMIN_API_KEY=...                # required to use the /tenants endpoints
uv run uvicorn app.main:create_app --factory
```

- `GET /health` → `{"status": "ok"}`
- `POST /chat` with `{"session_id": "...", "message": "..."}` →
  `{"session_id", "reply", "guardrails": [{"name", "action"}]}`;
  blocked topics return 403 `{"error": "blocked", "guardrail": "topic_blocklist"}`.
  The optional `X-Tenant-ID` header picks the tenant (default `"default"`)
  whose policy is applied.

## Tenant policies

A policy is
`{"blocked_topics": [str], "redact_pii": bool, "max_tool_calls": int, "system_prompt": str | null}`.
A tenant's `system_prompt`, when set, is appended to the agent's instructions.
Tenants without a stored policy use the default: blocked topics and tool-call
limit from `AGENT_BLOCKED_TOPICS` / `AGENT_MAX_TOOL_CALLS` (so
`["weapons", "malware"]` and 5 unless configured), `redact_pii` true, no
system prompt.

All of these need `X-Admin-Key: $ADMIN_API_KEY` (otherwise 401; if
`ADMIN_API_KEY` is unset they always return 401):

- `PUT /tenants/{tenant_id}/policy` with a full policy → 200 with the stored
  policy; 422 for an invalid body (missing/unknown fields, wrong types,
  negative limit).
- `GET /tenants/{tenant_id}/policy` → 200 with the effective policy.
- `DELETE /tenants/{tenant_id}/policy` → 204; the tenant reverts to the default.

Policies are kept in memory (lost on restart) behind the `PolicyStore`
protocol in `app/policies.py`, which a database-backed store can implement.

## Configuration (environment variables)

| Variable               | Default                       | Meaning                                  |
|------------------------|-------------------------------|------------------------------------------|
| `AGENT_MODEL`          | `anthropic:claude-sonnet-5-5` | `provider:model` for `init_chat_model`   |
| `AGENT_BLOCKED_TOPICS` | `weapons,malware`             | Default policy's topics (comma-separated)|
| `AGENT_MAX_TOOL_CALLS` | `5`                           | Default policy's tool calls per turn     |
| `AGENT_SYSTEM_PROMPT`  | (built-in)                    | System prompt for the agent              |
| `ADMIN_API_KEY`        | (unset: admin API disabled)   | Key for the `/tenants` endpoints         |

## Test

```sh
uv run pytest
```

Tests use LangChain fake chat models only; no API key is needed.
