# Guarded deep agent

FastAPI service wrapping a LangChain `deepagents` agent with guardrails:
PII redaction, a topic blocklist and a per-turn tool-call limit, configured
per tenant. Conversations are remembered per session, and replies can be
streamed.

## Run

```bash
uv sync
export ANTHROPIC_API_KEY=...            # or the key for whichever provider you pick
export AGENT_MODEL=anthropic:claude-sonnet-5-5   # any init_chat_model "provider:model" string
export ADMIN_API_KEY=change-me          # required to manage tenant policies
uv run uvicorn app.main:create_app --factory
```

```bash
curl localhost:8000/health
curl -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"session_id": "s1", "message": "What is 17 * 23?"}'
```

| Variable          | Default                       | Meaning                                         |
|-------------------|-------------------------------|-------------------------------------------------|
| `AGENT_MODEL`     | `anthropic:claude-sonnet-5-5` | Model used when `create_app()` gets no model    |
| `BLOCKED_TOPICS`  | `weapons,malware`             | Comma-separated; empty string disables          |
| `TOOL_CALL_LIMIT` | `5`                           | Max tool calls per turn (subagent calls count)  |
| `ADMIN_API_KEY`   | unset                         | `X-Admin-Key` for `/tenants/*`; unset = all 401 |

`BLOCKED_TOPICS` and `TOOL_CALL_LIMIT` set the **default policy**.

## Tenant policies

`/chat` takes the tenant from the `X-Tenant-ID` header (`default` if absent) and
applies that tenant's policy, or the default policy if none is stored:

```json
{"blocked_topics": ["weapons", "malware"], "redact_pii": true,
 "max_tool_calls": 5, "system_prompt": null}
```

`system_prompt`, when set, is appended to the agent's instructions (main agent
and subagent). Limits: up to 100 topics of up to 100 chars, `max_tool_calls`
0–100, `system_prompt` up to 8000 chars; unknown fields are rejected.

```bash
H='X-Admin-Key: change-me'
curl -X PUT localhost:8000/tenants/acme/policy -H "$H" -H 'content-type: application/json' \
  -d '{"blocked_topics": ["gambling"], "redact_pii": false, "max_tool_calls": 3, "system_prompt": "Answer in French."}'
curl localhost:8000/tenants/acme/policy -H "$H"            # effective policy
curl -X DELETE localhost:8000/tenants/acme/policy -H "$H"  # back to default
curl -X POST localhost:8000/chat -H 'X-Tenant-ID: acme' -H 'content-type: application/json' \
  -d '{"session_id": "s1", "message": "hi"}'
```

Policies live in memory (per process, lost on restart). Storage goes through
the `PolicyStore` protocol in `app/policies.py`; a database-backed store can
replace `app.state.policy_store`.

## Conversations and streaming

Turns sent with the same `session_id` (under the same `X-Tenant-ID`) form one
conversation: the model sees the earlier user messages and replies, as
redacted under the tenant's policy. Messages refused by the blocklist and
turns that fail are not stored.

```bash
curl localhost:8000/sessions/s1/messages            # 200 {"session_id", "messages": [...]}, or 404
curl -X DELETE localhost:8000/sessions/s1           # 204, or 404 if unknown
curl -N -X POST localhost:8000/chat/stream -H 'content-type: application/json' \
  -d '{"session_id": "s1", "message": "And times 2?"}'
# data: {"type": "token", "content": "It "}
# ...
# data: {"type": "done", "session_id": "s1", "guardrails": []}
```

`/chat/stream` runs the turn to completion before sending tokens: only then is
it known which message is the reply (text a model writes alongside a tool call
is not part of it), so the tokens always add up to exactly what `/chat` would
return, already redacted. Blocked messages (403) and agent failures (502) get
the same JSON as `/chat`.

Sessions live in memory (per process, lost on restart, unbounded). Storage goes
through the `SessionStore` protocol in `app/sessions.py`; a database-backed
store can replace `app.state.session_store`. Each turn is stored as one
user/assistant pair once it finishes, so concurrent turns on one session don't
see each other's in-flight messages.

## Test

```bash
uv run pytest
```

Tests use fake chat models only (`tests/fakes.py`); no API key is needed.
