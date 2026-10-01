# Guarded deep agent

A FastAPI service that serves a LangChain `deepagents` agent (with `calculator`
and `current_time` tools) behind three guardrails: PII redaction, a topic
blocklist and a per-turn tool-call limit.

## Run

```sh
uv sync
export ANTHROPIC_API_KEY=...            # or the key for whichever provider you configure
uv run uvicorn app.main:create_app --factory
```

- `GET /health` → `{"status": "ok"}`
- `POST /chat` with `{"session_id": "...", "message": "..."}` →
  `{"session_id", "reply", "guardrails": [{"name", "action"}]}`;
  blocked topics return 403 `{"error": "blocked", "guardrail": "topic_blocklist"}`.

## Configuration (environment variables)

| Variable               | Default                       | Meaning                                  |
|------------------------|-------------------------------|------------------------------------------|
| `AGENT_MODEL`          | `anthropic:claude-sonnet-5-5` | `provider:model` for `init_chat_model`   |
| `AGENT_BLOCKED_TOPICS` | `weapons,malware`             | Comma-separated, whole-word, any case    |
| `AGENT_MAX_TOOL_CALLS` | `5`                           | Max tool calls per turn                  |
| `AGENT_SYSTEM_PROMPT`  | (built-in)                    | System prompt for the agent              |

## Test

```sh
uv run pytest
```

Tests use LangChain fake chat models only; no API key is needed.
