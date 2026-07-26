# Rate limiter: operator notes

## What it is

`rate_limiter.py` is a token-bucket rate limiter for the API fleet. It is
stateless in-process: every worker uses the same shared `Store` to read and
write each API key's bucket, so a key's quota is enforced fleet-wide, not
per worker.

Usage:

```python
from rate_limiter import RateLimiter, RateLimitConfig

limiter = RateLimiter(
    store,
    default_config=RateLimitConfig.per_minute(60),        # 60 req/min, burst 60
    key_configs={"premium-key": RateLimitConfig.per_minute(600, burst=1000)},
)

decision = limiter.check(api_key)
if not decision.allowed:
    return 429_response(
        headers={
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
            "X-RateLimit-Reset": str(decision.reset_seconds),
        }
    )
```

`RateLimitConfig.per_minute(n)` and `.per_second(n)` are convenience
constructors; you can also construct `RateLimitConfig(rate=..., burst=...)`
directly with `rate` in tokens/second if you want a burst that differs from
the steady rate (e.g. rate 1/s with burst 20, to permit short spikes).

## How the bucket state is stored

Each API key maps to one store key (`f"{namespace}:{api_key}"`, namespace
defaults to `"ratelimit"`) holding 16 bytes: two packed big-endian doubles,
`(tokens_remaining, last_update_epoch_seconds)`. There's no background
refill job -- every call to `check()` computes how many tokens would have
accrued since `last_update`, caps at `burst`, then spends one token if the
request is allowed, and writes the new state back. A TTL is attached to
each `set()`, sized to roughly how long a full refill takes, so an idle
key's storage is eventually reclaimed; that expiry has no effect on
correctness, since an idle bucket would be back at full burst anyway.

## Why windows line up across workers

All timing is done in absolute wall-clock seconds (`time.time()`), read and
written straight to/from the shared store. There is no per-process
"minute since I started" or in-memory clock involved, so worker A and
worker B always agree on how much time has elapsed since a bucket was last
touched, regardless of which one touched it. This does depend on the
fleet's clocks being kept in sync (NTP or equivalent) -- a worker whose
clock is meaningfully ahead or behind the rest of the fleet will compute a
different, incorrect refill for the same key. The limiter clamps negative
elapsed time (clock skew or two calls landing in the same instant) to
avoid manufacturing tokens from a "negative" interval, but it can't correct
for skew beyond that.

## Concurrency: what is and isn't guaranteed

The `Store` interface we were given is `get` / `set` / `delete` only -- no
compare-and-swap, no atomic increment. That means a `check()` call is a
read, a compute, then a write, and nothing stops two workers from
interleaving: both read the same token count, both decide "allowed", both
write back independently. In that interleaving the second write clobbers
the first, and the fleet has effectively let through one extra request for
that instant -- the bucket briefly under-counts by however many requests
raced each other on the exact same key.

In practice this only matters for concurrent requests on the *same*
API key at the *same* instant, and it's a bounded, small effect (each race
costs at most one extra request per colliding pair), not an unbounded
leak. It's mitigated for the common case of one worker process serving
many concurrent threads or async tasks against a shared `RateLimiter`
instance: `check()` takes a lock striped by `hash(api_key)` (256 stripes,
fixed memory regardless of key cardinality) around its read-compute-write,
so same-process races are fully serialized. That lock is process-local and
does nothing across the two-plus separate worker processes actually
running the fleet, since each has its own lock table.

Closing the cross-process race requires an atomic primitive from the
store itself (Redis `INCR`/a Lua script, a database row lock with
`SELECT ... FOR UPDATE`, etc.), which the given `Store` protocol doesn't
expose. If the operator needs hard exactness under concurrent traffic to
a single key, the fix is to extend the store with a compare-and-swap or
atomic-increment primitive and swap the read-modify-write in `_load_tokens`
/ `check` for a use of it -- that's a store-level change, not something
achievable against `get`/`set`/`delete` alone. Given typical traffic
(requests to one key are not usually simultaneous down to the same
microsecond across processes), this is the same trade-off most
`get`/`set`-only distributed rate limiters make, and it errs in the safe
direction of very occasionally allowing one extra request rather than
ever wrongly rejecting one.

## `Decision.reset_seconds`

This is "seconds until the bucket is back at full burst capacity," not
"seconds until exactly one more token exists." It's monotonically
decreasing as the bucket refills and is `0.0` once full -- this is the
usual meaning of `X-RateLimit-Reset`. If you specifically want a
`Retry-After` for a 429 response (time until the *next* single token is
available), compute it from the same config:
`max(0.0, (1 - decision.remaining) / config.rate)`.

## Performance

`check()` does exactly one `store.get`, one `store.set`, a struct
pack/unpack, and a handful of float ops under a striped lock. Excluding
the store's own I/O latency (which is outside this module's control), the
limiter's own overhead is on the order of low microseconds -- well under
the sub-millisecond bar in the spec. The lock table is fixed-size (256
`threading.Lock`s), so memory use doesn't grow with the number of distinct
API keys seen.

## Other bits

- `RateLimiter.configure(api_key, config)` adds or replaces a per-key
  override at runtime (e.g. wiring in a new customer's plan without a
  restart -- note this only affects the process it's called on; push the
  same call to every worker, or load `key_configs` from shared config at
  startup, to keep them in agreement).
- `RateLimiter.reset(api_key)` deletes a key's stored bucket (e.g. a
  support override to immediately un-throttle someone); the next request
  reinitialises it to a full bucket.
- A corrupted or unrecognised value under a key's storage slot (wrong
  byte length) is treated as "no bucket yet" rather than raising, so a
  storage-layer hiccup fails open into "within quota" instead of taking
  requests down.
- `RateLimitConfig(rate=..., burst=...)` validates `rate > 0` and
  `burst > 0` at construction and raises `ValueError` otherwise.
