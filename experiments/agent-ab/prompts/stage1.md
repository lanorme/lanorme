Build a FastAPI service that serves a conversational agent built with LangChain's
`deepagents` library (`create_deep_agent`), with guardrails around it. The
working directory is empty apart from `CLAUDE.md`; create the project here.

## Contract

Other systems depend on this exactly; everything inside is your design.

- A uv project whose application package is `app/`. `app/main.py` exposes
  `create_app(model: BaseChatModel | None = None) -> FastAPI`. When `model` is
  given the agent uses it (tests pass a fake chat model that supports
  `bind_tools`); when it is `None`, build a real model from configuration (for
  example `init_chat_model` with a model name from an environment variable).
  Never create a real model at import time. `uv run uvicorn app.main:create_app
  --factory` must start the server.
- `GET /health` returns 200 `{"status": "ok"}`.
- `POST /chat` takes `{"session_id": str, "message": str}` and returns 200
  `{"session_id": str, "reply": str, "guardrails": [{"name": str, "action":
  str}]}`, where `guardrails` lists every guardrail that acted on this turn
  (empty when none did). A malformed body returns 422.

## Guardrails

1. **PII redaction** (`"name": "pii_redaction"`, `"action": "redacted"`).
   Email addresses and phone numbers in the user's message are replaced with
   `[REDACTED_EMAIL]` and `[REDACTED_PHONE]` before the model sees them, and
   the same redaction is applied to the model's reply before it is returned.
   Handle common formats such as `+44 20 7946 0958`, `(555) 123-4567` and
   `555-123-4567`.
2. **Topic blocklist** (`"name": "topic_blocklist"`, `"action": "blocked"`).
   If the message mentions a blocked topic (case-insensitive, whole words), the
   model is not called and the service returns 403 `{"error": "blocked",
   "guardrail": "topic_blocklist"}`. The list is configurable; the default is
   `["weapons", "malware"]`.
3. **Tool-call limit** (`"name": "tool_call_limit"`, `"action": "stopped"`).
   One turn may make at most N tool calls (configurable, default 5). When the
   agent goes past the limit, the turn stops and the service returns 200 with a
   reply saying the limit was reached, and the guardrail listed.

## Also

- Give the agent at least one tool of your own beyond the deepagents built-ins
  (a calculator, a clock, anything sensible).
- Configuration comes from environment variables.
- Each request is independent for now; conversation memory comes later.
- Test it well with fake models. When you are done, summarise what you built.
