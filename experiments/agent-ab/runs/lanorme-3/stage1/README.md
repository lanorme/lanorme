# Guarded agent service

A FastAPI service around a LangChain `deepagents` agent, with three guardrails:
PII redaction, a topic blocklist and a per-turn tool-call limit.

## Run the service

```console
uv sync
export ANTHROPIC_API_KEY=...            # or the key for the provider you pick
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

## API

- `GET /health` returns `{"status": "ok"}`.
- `POST /chat` takes `{"session_id": str, "message": str}` and returns
  `{"session_id", "reply", "guardrails": [{"name", "action"}]}`. A message on a
  blocked topic returns 403 `{"error": "blocked", "guardrail": "topic_blocklist"}`
  without calling the model. A malformed body returns 422.

Each request is independent; there is no conversation memory yet.

## Tests and linting

```console
uv run pytest
uvx lanorme@0.21.0 check .
```

The tests drive the agent with fake chat models and never call a real one.
