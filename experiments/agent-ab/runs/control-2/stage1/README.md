# Deep agent service

A FastAPI service around a LangChain `deepagents` agent, with guardrails for
PII redaction, a topic blocklist and a per-turn tool-call limit.

## Run

```sh
uv sync
export ANTHROPIC_API_KEY=...            # or the key for whichever provider you configure
uv run uvicorn app.main:create_app --factory
```

```sh
curl localhost:8000/health
curl -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"session_id": "s1", "message": "What is 17 * 23?"}'
```

## Configuration (environment variables)

| Variable              | Default                        | Meaning                                                    |
|-----------------------|--------------------------------|------------------------------------------------------------|
| `AGENT_MODEL`         | `anthropic:claude-sonnet-5-5`  | `provider:model` passed to `init_chat_model`               |
| `BLOCKED_TOPICS`      | `weapons,malware`              | Comma-separated words/phrases; empty disables the blocklist |
| `MAX_TOOL_CALLS`      | `5`                            | Tool calls allowed per turn, subagent calls included       |
| `AGENT_SYSTEM_PROMPT` | short built-in prompt          | System prompt for the agent                                |

## Tests

```sh
uv run pytest
```

The tests use LangChain's fake chat models and never call a real model.

## Layout

- `app/main.py`: `create_app(model=None)`, the HTTP layer
- `app/agent.py`: builds the deep agent and runs one guarded turn
- `app/guardrails.py`: PII redaction, topic blocklist, tool-call limit middleware
- `app/tools.py`: extra agent tools (`calculator`, `current_time`)
- `app/config.py`: settings from environment variables
