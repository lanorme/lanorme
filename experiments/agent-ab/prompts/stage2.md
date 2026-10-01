The service in this directory is a FastAPI app serving a `deepagents`
conversational agent behind guardrails. Read it first. Now add per-tenant
guardrail policies, keeping everything that already works working.

## Contract

Other systems depend on this exactly; everything inside is your design.

- A request names its tenant with the `X-Tenant-ID` header. Without it, the
  tenant is `"default"`.
- A policy is `{"blocked_topics": [str], "redact_pii": bool,
  "max_tool_calls": int, "system_prompt": str | null}`. A tenant with no stored
  policy uses the default policy: blocked topics `["weapons", "malware"]`,
  `redact_pii` true, `max_tool_calls` 5, no system prompt.
- `PUT /tenants/{tenant_id}/policy` with a full policy body stores it and
  returns 200 with the stored policy; an invalid body returns 422.
- `GET /tenants/{tenant_id}/policy` returns 200 with the tenant's effective
  policy (the default when none is stored).
- `DELETE /tenants/{tenant_id}/policy` returns 204; the tenant falls back to
  the default policy.
- The three tenant endpoints require an `X-Admin-Key` header equal to the
  `ADMIN_API_KEY` environment variable (read when the app is created); a
  missing or wrong key returns 401.
- `POST /chat` applies the calling tenant's policy: its blocked topics, whether
  PII is redacted (when `redact_pii` is false the message reaches the model
  unchanged and the reply is not redacted), its tool-call limit, and its system
  prompt, which when set is added to the agent's instructions.
- `create_app(model=...)` keeps its signature, and the stage-one responses and
  guardrail names are unchanged.

Keeping policies in memory is fine, behind an interface a database could
replace later. Test it well with fake models. When you are done, summarise what
you changed.
