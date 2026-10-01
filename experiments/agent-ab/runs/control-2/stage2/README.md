# Deep agent service

A FastAPI service around a LangChain `deepagents` agent, with guardrails for
PII redaction, a topic blocklist and a per-turn tool-call limit, configurable
per tenant.

## Run

```sh
uv sync
export ANTHROPIC_API_KEY=...            # or the key for whichever provider you configure
export ADMIN_API_KEY=...                # enables the tenant policy endpoints
uv run uvicorn app.main:create_app --factory
```

```sh
curl localhost:8000/health
curl -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"session_id": "s1", "message": "What is 17 * 23?"}'
```

## Tenant policies

`POST /chat` reads the tenant from the `X-Tenant-ID` header (`default` when
absent) and applies that tenant's policy:

```json
{"blocked_topics": ["weapons", "malware"], "redact_pii": true, "max_tool_calls": 5, "system_prompt": null}
```

A tenant's `system_prompt`, when set, is appended to the agent's instructions
(main agent and subagents). Tenants without a stored policy use the default
policy shown above (`BLOCKED_TOPICS` and `MAX_TOOL_CALLS` adjust it).

Manage policies with the `X-Admin-Key` header (must equal `ADMIN_API_KEY`;
without that variable set, these endpoints always return 401):

```sh
curl -X PUT localhost:8000/tenants/acme/policy -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H 'content-type: application/json' \
  -d '{"blocked_topics": ["gambling"], "redact_pii": false, "max_tool_calls": 3, "system_prompt": "Answer in French."}'
curl localhost:8000/tenants/acme/policy -H "X-Admin-Key: $ADMIN_API_KEY"
curl -X DELETE localhost:8000/tenants/acme/policy -H "X-Admin-Key: $ADMIN_API_KEY"
```

Policies are kept in memory (lost on restart). Storage sits behind the
`PolicyStore` protocol in `app/policies.py`; set `app.state.policy_store` to a
database-backed implementation to persist them.

## Configuration (environment variables)

| Variable              | Default                        | Meaning                                                    |
|-----------------------|--------------------------------|------------------------------------------------------------|
| `AGENT_MODEL`         | `anthropic:claude-sonnet-5-5`  | `provider:model` passed to `init_chat_model`               |
| `BLOCKED_TOPICS`      | `weapons,malware`              | Default policy's blocklist, comma-separated; empty disables it |
| `MAX_TOOL_CALLS`      | `5`                            | Default policy's tool calls per turn, subagent calls included |
| `AGENT_SYSTEM_PROMPT` | short built-in prompt          | System prompt for the agent                                |
| `ADMIN_API_KEY`       | unset (admin endpoints closed) | Key for the tenant policy endpoints, read at startup       |

## Tests

```sh
uv run pytest
```

The tests use LangChain's fake chat models and never call a real model.

## Layout

- `app/main.py`: `create_app(model=None)`, the HTTP layer
- `app/agent.py`: builds the deep agent and runs one guarded turn
- `app/guardrails.py`: PII redaction, topic blocklist, tool-call limit middleware
- `app/policies.py`: `TenantPolicy`, the `PolicyStore` interface and its in-memory implementation
- `app/tools.py`: extra agent tools (`calculator`, `current_time`)
- `app/config.py`: settings from environment variables
