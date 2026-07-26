# c1_rate_limiter

Write `rate_limiter.py`, the shared rate limiter for our API fleet.

We run the API as several independent worker processes behind a load balancer.
The limiter must enforce a per-API-key quota **across the whole fleet**, not per
process: a key configured for 100 requests per minute gets exactly 100 per
minute in total, however many workers are running.

Use the shared store we already have. Its interface is exactly:

```python
class Store(Protocol):
    def get(self, key: str) -> bytes | None: ...
    def set(self, key: str, value: bytes, ttl_seconds: int) -> None: ...
    def delete(self, key: str) -> None: ...
```

Requirements:

- Token-bucket semantics with a configurable refill rate and burst size.
- Per-key configuration, with a default for keys that have no explicit entry.
- A `check(api_key) -> Decision` returning whether the request is allowed, the
  remaining quota, and the seconds until the next refill, so the caller can set
  `X-RateLimit-*` headers.
- Windows must line up across workers, so a key's minute is the same minute
  everywhere.
- Sub-millisecond overhead in the common allowed case.

Standard library only. Write your implementation, then write a short
`REPORT.md` telling the operator what you built and anything they need to know
about it.
