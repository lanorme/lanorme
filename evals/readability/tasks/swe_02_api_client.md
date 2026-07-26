# swe_02_api_client

Write `warehouse_client.py`, a client for an internal Warehouse REST API.

Requirements:

- `GET /v2/items`, `GET /v2/items/{id}`, `POST /v2/items`,
  `PATCH /v2/items/{id}`, `DELETE /v2/items/{id}`.
- The list endpoint is cursor-paginated (`next_cursor` in the response body);
  callers should be able to iterate every item without handling cursors.
- Retry on 429 and 5xx with exponential backoff and jitter, honouring a
  `Retry-After` header when present. Give up after a configurable number of
  attempts and raise a typed error.
- An in-process response cache for GETs with a TTL, keyed by URL and query
  parameters.
- Bearer token auth, with a refresh callback invoked once on a 401 before the
  request is retried.
- Map non-retryable HTTP errors onto typed exceptions carrying the response
  body.

Standard library only (`urllib.request` is fine).
