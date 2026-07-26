"""Fleet-wide token-bucket rate limiter.

We run the API as several independent worker processes behind a load
balancer, and a per-API-key quota has to hold across the whole fleet, not
per process. This module gets that by keeping *no* rate-limit state in the
worker process itself: every bit of state (the token count and the wall-clock
time it was last topped up) lives in the shared `Store`, keyed only by the
API key. Two workers checking the same key are therefore reading and writing
the exact same record, so there is nothing "per worker" to drift out of
sync -- a key's window is the same window everywhere, because there is only
ever one copy of it.

This does rely on worker clocks being reasonably in sync (NTP), since the
timestamp written by one worker is read and interpreted by another. That is
the standard assumption for a distributed fleet and is out of scope here.

Concurrency caveat
------------------
The `Store` we are given exposes only `get` / `set` / `delete`: no
compare-and-swap, no atomic increment. A `check()` call is therefore a
read-modify-write, not an atomic operation. If two workers call `check()` for
the *same* key at almost the same instant, both may read the same starting
state, both decide to allow the request, and the second write clobbers the
first -- so under concurrent bursts against one key, a handful of extra
requests can slip through beyond the configured quota. There is no way to
close that gap with only get/set/delete; doing so would need the store to
offer a compare-and-swap or an atomic increment primitive. What this
implementation does do is keep the race window as small as possible (a
single get, some arithmetic, a single set -- no extra round trips) and never
lets the race cause *under*-granting or a stuck/negative bucket.

Usage
-----
    limiter = RateLimiter(store, default=RateLimit.per_minute(60))
    limiter.set_limit("key-for-big-customer", RateLimit.per_minute(6000, burst=200))

    decision = limiter.check(api_key)
    headers = {
        "X-RateLimit-Remaining": str(decision.remaining),
        "X-RateLimit-Reset": str(decision.retry_after),
    }
    if not decision.allowed:
        return too_many_requests(retry_after=decision.retry_after)
"""

from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass
from typing import Mapping, Protocol


class Store(Protocol):
    """The shared, fleet-wide key/value store this limiter is built on."""

    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, ttl_seconds: int) -> None: ...

    def delete(self, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class RateLimit:
    """A token-bucket configuration for one key (or the default).

    `rate` is the steady-state refill rate in tokens (requests) per second.
    `burst` is the bucket capacity: how many requests a key can make
    back-to-back before it has to start waiting on the refill rate.
    """

    rate: float
    burst: float

    def __post_init__(self) -> None:
        if not self.rate > 0:
            raise ValueError(f"rate must be > 0, got {self.rate!r}")
        if not self.burst > 0:
            raise ValueError(f"burst must be > 0, got {self.burst!r}")

    @classmethod
    def per_second(cls, requests: float, burst: float | None = None) -> RateLimit:
        """`requests` per second. `burst` defaults to `requests`."""
        return cls(rate=requests, burst=requests if burst is None else burst)

    @classmethod
    def per_minute(cls, requests: float, burst: float | None = None) -> RateLimit:
        """`requests` per minute. `burst` defaults to `requests`.

        E.g. `RateLimit.per_minute(100)` allows 100 requests/minute, evenly
        refilling, with bursts of up to 100 at once.
        """
        return cls(rate=requests / 60.0, burst=requests if burst is None else burst)


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of a `RateLimiter.check()` call."""

    allowed: bool
    """Whether this request is within quota and should proceed."""

    remaining: int
    """Requests immediately available right now, after this one."""

    retry_after: float
    """Seconds until at least one more request is available.

    `0.0` whenever `remaining > 0`. Suitable for both a `Retry-After` header
    on a 429 and an `X-RateLimit-Reset` hint on a successful response.
    """


# Packed state stored per key: (tokens, last_refill_epoch_seconds), 16 bytes,
# fixed layout so encode/decode is a single struct call with no branching.
_STATE = struct.Struct("!dd")

_KEY_PREFIX = "ratelimit:v1:"


class RateLimiter:
    """Fleet-wide token-bucket limiter backed by a shared `Store`.

    All state lives in the store under `check()`; the limiter instance
    itself only holds configuration (the default limit and any per-key
    overrides), so it is cheap to construct per-process and needs no
    coordination beyond the shared store.
    """

    def __init__(
        self,
        store: Store,
        default: RateLimit,
        limits: Mapping[str, RateLimit] | None = None,
    ) -> None:
        self._store = store
        self._default = default
        self._limits: dict[str, RateLimit] = dict(limits) if limits else {}

    def set_limit(self, api_key: str, limit: RateLimit) -> None:
        """Install (or replace) a per-key override."""
        self._limits[api_key] = limit

    def clear_limit(self, api_key: str) -> None:
        """Drop a per-key override; the key falls back to the default."""
        self._limits.pop(api_key, None)

    def check(self, api_key: str) -> Decision:
        """Consume one token for `api_key` if available, and report the result.

        This does one `store.get`, some arithmetic, and one `store.set` --
        no extra round trips -- so beyond the store's own I/O latency, the
        added overhead is a handful of float operations and a 16-byte
        struct pack/unpack: well under a millisecond in the common,
        allowed case.
        """
        limit = self._limits.get(api_key, self._default)
        now = time.time()
        store_key = _KEY_PREFIX + api_key

        raw = self._store.get(store_key)
        if raw is None:
            # Never seen (or expired -- see the TTL note below): starts full.
            tokens = limit.burst
        else:
            tokens, last_refill = _STATE.unpack(raw)
            elapsed = now - last_refill
            if elapsed > 0:
                tokens = min(limit.burst, tokens + elapsed * limit.rate)
            # elapsed <= 0 can only happen from clock skew between workers;
            # skip the refill rather than risk manufacturing tokens.

        if tokens >= 1.0:
            allowed = True
            tokens -= 1.0
        else:
            allowed = False

        # Keep the entry alive at least as long as a full refill would take,
        # plus a small safety margin. That way, if the entry does expire, the
        # bucket would have been full again anyway -- a missing key is never
        # a shortcut to *more* tokens than waiting would have earned.
        ttl_seconds = max(1, math.ceil(limit.burst / limit.rate) + 1)
        self._store.set(store_key, _STATE.pack(tokens, now), ttl_seconds)

        retry_after = 0.0 if tokens >= 1.0 else (1.0 - tokens) / limit.rate
        return Decision(allowed=allowed, remaining=int(tokens), retry_after=retry_after)
