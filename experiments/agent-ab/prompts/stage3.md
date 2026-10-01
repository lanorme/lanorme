The service in this directory is a FastAPI app serving a `deepagents`
conversational agent behind per-tenant guardrail policies. Read it first. Now
add conversation memory and streaming, keeping everything that already works
working.

## Contract

Other systems depend on this exactly; everything inside is your design.

### Memory

- Turns sent with the same `session_id` continue one conversation: the model
  sees the earlier turns (as redacted under the tenant's policy). Sessions are
  scoped to the tenant, so the same `session_id` under two tenants is two
  separate conversations.
- A message refused by the topic blocklist is not stored.
- `GET /sessions/{session_id}/messages` (tenant from `X-Tenant-ID`) returns 200
  `{"session_id": str, "messages": [{"role": "user" | "assistant", "content":
  str}]}` in order, with content as stored (redacted where the policy
  redacts). An unknown session returns 404.
- `DELETE /sessions/{session_id}` (tenant from `X-Tenant-ID`) returns 204 and
  forgets the conversation; an unknown session returns 404.

### Streaming

- `POST /chat/stream` takes the same body and headers as `POST /chat` and
  returns `text/event-stream`. Each event is one `data: <json>` line followed
  by a blank line: `{"type": "token", "content": str}` for each chunk of the
  reply, then one final `{"type": "done", "session_id": str, "guardrails":
  [...]}`.
- The concatenated token contents equal the reply `POST /chat` would have
  returned, so output redaction must hold even when an email or phone number
  is split across chunks.
- A blocked message returns the same 403 JSON as `POST /chat`, with no stream.
- Streamed turns are stored in the conversation like any other.

In-memory storage is fine, behind an interface a database could replace later.
Test it well with fake models. When you are done, summarise what you changed.
