# Guarded agent

A FastAPI service that serves a conversational agent built with deepagents
(`create_deep_agent`), wrapped in three guardrails: PII redaction, a topic
blocklist and a per-turn tool-call limit.

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

## Endpoints

- `GET /health` returns `{"status": "ok"}`.
- `POST /chat` takes `{"session_id": str, "message": str}` and returns
  `{"session_id", "reply", "guardrails": [{"name", "action"}]}`. A blocked
  topic returns 403 `{"error": "blocked", "guardrail": "topic_blocklist"}`;
  a model failure returns 502 `{"error": "agent_unavailable"}`.

Each request is independent: there is no conversation memory yet.

## Run the tests and the linter

```sh
uv run pytest
uvx lanorme@0.21.0 check .
```

The tests drive the real deep agent with a scripted fake chat model, so they
need no API key and make no network calls.

## Layout

- `app/domain`: guardrail rules (PII redaction, topic blocklist) and errors.
- `app/application`: the `ChatAgent` port and the `ChatService` that applies
  the guardrails around one turn.
- `app/infrastructure`: settings, the agent's own tools (calculator, clock)
  and the deepagents adapter, which enforces the tool-call limit.
- `app/api`: HTTP routes and schemas.
- `app/main.py`: `create_app`, the composition root.
