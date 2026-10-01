# Guarded deep agent

A FastAPI service around a LangChain `deepagents` agent, with three guardrails:
PII redaction (emails and phone numbers, both directions), a topic blocklist
(403 without calling the model) and a per-turn tool-call limit that also covers
the agent's subagents. The agent has two custom tools: `calculator` and
`current_time`.

## Run

```sh
uv sync
export ANTHROPIC_API_KEY=...            # or the key for whichever provider AGENT_MODEL uses
uv run uvicorn app.main:create_app --factory
```

| Variable                | Default                       | Meaning                                  |
| ----------------------- | ----------------------------- | ---------------------------------------- |
| `AGENT_MODEL`           | `anthropic:claude-sonnet-5-5` | Model id passed to `init_chat_model`      |
| `AGENT_BLOCKED_TOPICS`  | `weapons,malware`             | Comma-separated blocked words            |
| `AGENT_TOOL_CALL_LIMIT` | `5`                           | Max tool calls per turn (subagents count) |

```sh
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"session_id": "s1", "message": "What is 17 * 23?"}'
```

## Test and lint

```sh
uv run pytest
uvx lanorme@0.21.0 check .
```

Tests use LangChain fake chat models; no API key is needed.
