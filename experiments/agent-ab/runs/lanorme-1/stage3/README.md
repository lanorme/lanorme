# Guarded deep agent

A FastAPI service around a LangChain `deepagents` agent, with three guardrails:
PII redaction (emails and phone numbers, both directions), a topic blocklist
(403 without calling the model) and a per-turn tool-call limit that also covers
the agent's subagents. The agent has two custom tools: `calculator` and
`current_time`.

Guardrails are set per tenant. `POST /chat` reads the tenant from the
`X-Tenant-ID` header (`default` when absent) and applies that tenant's policy:

```json
{"blocked_topics": ["weapons", "malware"], "redact_pii": true,
 "max_tool_calls": 5, "system_prompt": null}
```

A tenant with no stored policy uses the default above (built from
`AGENT_BLOCKED_TOPICS` and `AGENT_TOOL_CALL_LIMIT`). `system_prompt`, when set,
is appended to the agent's instructions. Policies are kept in memory and are
lost on restart.

| Endpoint                              | Effect                                   |
| ------------------------------------- | ---------------------------------------- |
| `GET /tenants/{tenant_id}/policy`     | Effective policy (default if none stored) |
| `PUT /tenants/{tenant_id}/policy`     | Store a full policy (422 if invalid)      |
| `DELETE /tenants/{tenant_id}/policy`  | Drop it; 204, back to the default         |

These need `X-Admin-Key` equal to `ADMIN_API_KEY` (401 otherwise; with
`ADMIN_API_KEY` unset, every request is refused).

## Conversations and streaming

Turns with the same `session_id` continue one conversation, scoped to the
tenant: the model sees the earlier turns as they were stored (redacted when the
tenant redacts; redacted again if the tenant has since turned redaction on).
A message refused by the blocklist, a turn whose model call fails and a stream
the client abandons are not stored. Conversations are kept in memory and are
lost on restart.

| Endpoint                          | Effect                                                    |
| --------------------------------- | --------------------------------------------------------- |
| `POST /chat/stream`               | Same body, headers and 403 as `/chat`; replies as `text/event-stream` |
| `GET /sessions/{id}/messages`     | `{"session_id", "messages": [{"role", "content"}]}`; 404 if unknown |
| `DELETE /sessions/{id}`           | Forget the conversation; 204, or 404 if unknown            |

The session endpoints take the tenant from `X-Tenant-ID`, like `/chat`. The
stream sends `data: {"type": "token", "content": ...}` events, then one
`data: {"type": "done", "session_id": ..., "guardrails": [...]}`. The tokens
join to exactly the reply `/chat` would give: both endpoints run the same
pipeline, and output redaction holds text back only until no email or phone
number can still be in progress. When the agent writes text in several model
calls of one turn (say, "Let me check." before a tool call), the reply is all
of it, joined by a blank line.

## Run

```sh
uv sync
export ANTHROPIC_API_KEY=...            # or the key for whichever provider AGENT_MODEL uses
export ADMIN_API_KEY=...                # for the tenant policy endpoints
uv run uvicorn app.main:create_app --factory
```

| Variable                | Default                       | Meaning                                  |
| ----------------------- | ----------------------------- | ---------------------------------------- |
| `AGENT_MODEL`           | `anthropic:claude-sonnet-5-5` | Model id passed to `init_chat_model`      |
| `AGENT_BLOCKED_TOPICS`  | `weapons,malware`             | Default policy's blocked words, comma-separated |
| `AGENT_TOOL_CALL_LIMIT` | `5`                           | Default policy's max tool calls per turn (subagents count) |
| `ADMIN_API_KEY`         | unset                         | Key for the tenant policy endpoints       |

```sh
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -H 'X-Tenant-ID: acme' -d '{"session_id": "s1", "message": "What is 17 * 23?"}'

curl -sN localhost:8000/chat/stream -H 'content-type: application/json' \
  -H 'X-Tenant-ID: acme' -d '{"session_id": "s1", "message": "And times 2?"}'

curl -s localhost:8000/sessions/s1/messages -H 'X-Tenant-ID: acme'

curl -s -X PUT localhost:8000/tenants/acme/policy -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H 'content-type: application/json' \
  -d '{"blocked_topics": ["crypto"], "redact_pii": false, "max_tool_calls": 2,
       "system_prompt": "Answer as the Acme help desk."}'
```

## Test and lint

```sh
uv run pytest
uvx lanorme@0.21.0 check .
```

Tests use LangChain fake chat models; no API key is needed.
