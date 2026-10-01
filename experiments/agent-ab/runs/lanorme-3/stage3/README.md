# Guarded agent service

A FastAPI service around a LangChain `deepagents` agent, with three guardrails:
PII redaction, a topic blocklist and a per-turn tool-call limit. Each tenant can
have its own guardrail policy. Turns in the same session form one conversation,
and replies can be streamed.

## Run the service

```console
uv sync
export ANTHROPIC_API_KEY=...            # or the key for the provider you pick
export ADMIN_API_KEY=...                # guards the tenant policy endpoints
uv run uvicorn app.main:create_app --factory
```

Then:

```console
curl localhost:8000/health
curl -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"session_id": "s1", "message": "What is 12.5 * 4?"}'
```

## Configuration

All settings are environment variables, read when the app is created.

| Variable         | Default                       | Meaning                                                    |
| ---------------- | ----------------------------- | ---------------------------------------------------------- |
| `AGENT_MODEL`    | `anthropic:claude-sonnet-5-5` | Any model string `init_chat_model` accepts                 |
| `BLOCKED_TOPICS` | `weapons,malware`             | Comma-separated topics; an empty value blocks nothing      |
| `MAX_TOOL_CALLS` | `5`                           | Most tool calls one turn may make (a positive integer)     |
| `ADMIN_API_KEY`  | unset                         | Key for the tenant endpoints; unset or empty refuses all   |

`BLOCKED_TOPICS` and `MAX_TOOL_CALLS` shape the default policy, which applies
to every tenant without a stored policy of its own.

## API

- `GET /health` returns `{"status": "ok"}`.
- `POST /chat` takes `{"session_id": str, "message": str}` and returns
  `{"session_id", "reply", "guardrails": [{"name", "action"}]}`. A message on a
  blocked topic returns 403 `{"error": "blocked", "guardrail": "topic_blocklist"}`
  without calling the model. A malformed body returns 422. The `X-Tenant-ID`
  header picks the tenant whose policy applies (`default` when it is absent).
- `POST /chat/stream` takes the same body and headers as `POST /chat` and
  returns `text/event-stream`: one `data: {"type": "token", "content": str}`
  event per chunk of the reply, then `data: {"type": "done", "session_id",
  "guardrails"}`. The tokens join into exactly the reply `POST /chat` would
  give, redaction included. A blocked message gets the same 403 JSON. Only the
  agent's final answer is streamed, so tokens start once the model has
  finished that answer (text it writes before calling a tool is never sent).
  A stream that ends without `done` failed.
- Turns sent with the same `session_id` continue one conversation, scoped to
  the tenant; the model sees the earlier turns as stored (redacted under the
  tenant's policy). A blocked message is not stored.
- `GET /sessions/{session_id}/messages` returns `{"session_id", "messages":
  [{"role": "user" | "assistant", "content"}]}` in order, and `DELETE
  /sessions/{session_id}` forgets the conversation (204). Both use
  `X-Tenant-ID` like `/chat` and return 404 for an unknown session.
- `GET`, `PUT` and `DELETE /tenants/{tenant_id}/policy` read, replace and reset
  a tenant's policy. They need `X-Admin-Key` equal to `ADMIN_API_KEY`, else 401.
  A policy is `{"blocked_topics": [str], "redact_pii": bool, "max_tool_calls":
  int, "system_prompt": str | null}`; PUT takes all four fields and returns 422
  for an invalid body. `DELETE` returns 204 and the tenant falls back to the
  default policy.

```console
curl -X PUT localhost:8000/tenants/acme/policy -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H 'content-type: application/json' -d '{"blocked_topics": ["gambling"],
  "redact_pii": false, "max_tool_calls": 3, "system_prompt": "Answer in French."}'
curl -X POST localhost:8000/chat -H 'X-Tenant-ID: acme' -H 'content-type: application/json' \
  -d '{"session_id": "s1", "message": "Bonjour"}'
```

Policies and conversations are kept in memory and lost on restart.
`create_app(policy_store=..., conversation_store=...)` accepts any
`app.policies.PolicyStore` and `app.conversations.ConversationStore`, the
seams for a database.

```console
curl -N -X POST localhost:8000/chat/stream -H 'X-Tenant-ID: acme' \
  -H 'content-type: application/json' -d '{"session_id": "s1", "message": "And in English?"}'
curl localhost:8000/sessions/s1/messages -H 'X-Tenant-ID: acme'
curl -X DELETE localhost:8000/sessions/s1 -H 'X-Tenant-ID: acme'
```

## Tests and linting

```console
uv run pytest
uvx lanorme@0.21.0 check .
```

The tests drive the agent with fake chat models and never call a real one.
