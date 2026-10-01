# Guarded deep agent

FastAPI service wrapping a LangChain `deepagents` agent with guardrails:
PII redaction, a topic blocklist and a per-turn tool-call limit.

## Run

```bash
uv sync
export ANTHROPIC_API_KEY=...            # or the key for whichever provider you pick
export AGENT_MODEL=anthropic:claude-sonnet-5-5   # any init_chat_model "provider:model" string
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

## Test

```bash
uv run pytest
```

Tests use fake chat models only (`tests/fakes.py`); no API key is needed.
