"""Distributed token-bucket rate limiter for a multi-process API fleet.

The service runs as several independent worker processes behind a load
balancer. Each worker imports this module and shares one external ``Store``
(Redis or similar), so a per-API-key quota is enforced across the whole
fleet rather than once per process.

Design summary
---------------
* **Algorithm**: classic token bucket, computed lazily. No background
  refill thread is needed: each call to :meth:`RateLimiter.check` computes
  how many tokens would have accumulated since the bucket's state was last
  written, caps that at the configured burst size, and only then applies
  the cost of the current request.
* **Clock**: all timestamps are absolute wall-clock seconds
  (``time.time()``), never a process-relative clock. That is what makes a
  key's bucket line up across workers -- worker A and worker B agree on
  "how much time has passed since the bucket was last touched" because
  they are both measuring from the same epoch, not from when their own
  process started. This assumes worker clocks are kept in sync (NTP or
  equivalent); see REPORT.md for the operational implications.
* **Storage**: one key per API key in the shared store, holding 16 bytes
  (two packed IEEE-754 doubles: current token count and the wall-clock
  time it was last updated). A TTL is attached so idle keys age out
  instead of accumulating forever.
* **Concurrency**: the ``Store`` protocol we were given exposes only
  ``get``/``set``/``delete`` -- no compare-and-swap or atomic increment.
  See the "Concurrency" note in REPORT.md for exactly what that does and
  does not guarantee.
"""

from __future__ import annotations

import math
import struct
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "Store",
    "RateLimitConfig",
    "Decision",
    "RateLimiter",
]


class Store(Protocol):
    """The shared, fleet-wide key/value store this limiter is built on."""

    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, ttl_seconds: int) -> None: ...

    def delete(self, key: str) -> None: ...


@dataclass(frozen=True)
class RateLimitConfig:
    """A token bucket's shape: how fast it refills and how big it is.

    Attributes:
        rate: tokens (requests) added per second.
        burst: maximum tokens the bucket can hold, i.e. the size of the
            allowance a caller can spend in a single burst after being
            idle. This is also reported as ``Decision.limit``.
    """

    rate: float
    burst: float

    def __post_init__(self) -> None:
        if not self.rate > 0:
            raise ValueError(f"rate must be > 0, got {self.rate!r}")
        if not self.burst > 0:
            raise ValueError(f"burst must be > 0, got {self.burst!r}")

    @classmethod
    def per_minute(cls, requests_per_minute: float, burst: float | None = None) -> RateLimitConfig:
        """Build a config from a "N requests per minute" quota.

        ``burst`` defaults to ``requests_per_minute`` (a caller who has been
        idle can spend the whole minute's allowance at once); pass an
        explicit value to allow a bigger or smaller burst than the steady
        rate.
        """
        return cls(rate=requests_per_minute / 60.0, burst=requests_per_minute if burst is None else burst)

    @classmethod
    def per_second(cls, requests_per_second: float, burst: float | None = None) -> RateLimitConfig:
        """Build a config from a "N requests per second" quota."""
        return cls(rate=requests_per_second, burst=requests_per_second if burst is None else burst)


@dataclass(frozen=True)
class Decision:
    """The outcome of a single :meth:`RateLimiter.check` call.

    Attributes:
        allowed: whether this request should proceed.
        limit: the bucket's burst capacity (for ``X-RateLimit-Limit``).
        remaining: tokens left in the bucket *after* this request was
            accounted for, floored to an integer (for
            ``X-RateLimit-Remaining``). ``0`` when the request was denied.
        reset_seconds: seconds until the bucket is back at full capacity,
            assuming no further requests arrive (for ``X-RateLimit-Reset``).
            ``0.0`` if it is already full. Note this is "time to fully
            refilled", not "time until exactly one more token exists" --
            if you need a Retry-After for a 429 response, use
            ``max(0.0, (1 - remaining) / config.rate)`` with the caller's
            own config, or see REPORT.md for the one-line helper.
    """

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: float


# Two big-endian doubles: (tokens, last_update_epoch_seconds).
_STATE_STRUCT = struct.Struct(">dd")

# Fixed-size lock table, striped by hash(api_key), so memory use does not
# grow with the number of distinct API keys ever seen.
_LOCK_STRIPES = 256


class RateLimiter:
    """Fleet-wide token-bucket limiter backed by a shared :class:`Store`."""

    def __init__(
        self,
        store: Store,
        default_config: RateLimitConfig,
        *,
        key_configs: Mapping[str, RateLimitConfig] | None = None,
        namespace: str = "ratelimit",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Create a limiter.

        Args:
            store: the shared, fleet-wide store described above.
            default_config: the bucket shape used for any API key that has
                no entry in ``key_configs``.
            key_configs: per-key overrides of ``default_config``.
            namespace: prefix applied to every storage key, so this
                limiter's entries cannot collide with unrelated data in a
                store shared with other subsystems.
            clock: source of the current wall-clock time, in epoch
                seconds. Overridable for tests; must return a value that
                means the same thing on every worker (i.e. real wall-clock
                time, not a monotonic or process-relative clock).
        """
        self._store = store
        self._default_config = default_config
        self._key_configs: dict[str, RateLimitConfig] = dict(key_configs) if key_configs else {}
        self._namespace = namespace
        self._clock = clock
        self._locks = [threading.Lock() for _ in range(_LOCK_STRIPES)]

    def configure(self, api_key: str, config: RateLimitConfig) -> None:
        """Add or replace a per-key override at runtime."""
        self._key_configs[api_key] = config

    def reset(self, api_key: str) -> None:
        """Administratively clear a key's bucket (e.g. support override).

        The next request for this key will see a full, freshly-initialised
        bucket.
        """
        self._store.delete(self._storage_key(api_key))

    def check(self, api_key: str) -> Decision:
        """Decide whether a request for ``api_key`` is allowed right now.

        This does exactly one read and one write against the store, so its
        own overhead (excluding the store round-trip itself) is a handful
        of arithmetic operations and a struct pack/unpack -- well under a
        millisecond.
        """
        config = self._config_for(api_key)
        storage_key = self._storage_key(api_key)
        now = self._clock()

        # A per-process lock keeps two threads (or async tasks) in *this*
        # worker from racing each other on the same key. It does nothing
        # for races between separate worker processes -- see REPORT.md.
        with self._lock_for(api_key):
            tokens = self._load_tokens(storage_key, config, now)
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            self._store.set(storage_key, _STATE_STRUCT.pack(tokens, now), self._ttl_seconds(config))

        return self._build_decision(allowed, tokens, config)

    def _config_for(self, api_key: str) -> RateLimitConfig:
        return self._key_configs.get(api_key, self._default_config)

    def _lock_for(self, api_key: str) -> threading.Lock:
        return self._locks[hash(api_key) % _LOCK_STRIPES]

    def _storage_key(self, api_key: str) -> str:
        return f"{self._namespace}:{api_key}"

    def _load_tokens(self, storage_key: str, config: RateLimitConfig, now: float) -> float:
        raw = self._store.get(storage_key)
        if raw is None:
            return config.burst

        try:
            tokens, last_update = _STATE_STRUCT.unpack(raw)
        except struct.error:
            # Corrupt or foreign value under our namespace: treat as a
            # fresh bucket rather than raising, so a storage hiccup fails
            # open into "well within quota" instead of crashing requests.
            return config.burst

        elapsed = now - last_update
        if elapsed <= 0:
            # Clock went backwards (skew) or two calls landed in the same
            # instant; don't manufacture tokens out of negative time.
            return min(tokens, config.burst)
        return min(config.burst, tokens + elapsed * config.rate)

    def _ttl_seconds(self, config: RateLimitConfig) -> int:
        # Long enough that a bucket isn't evicted before it would have
        # refilled to full anyway -- expiry then has no observable effect
        # other than freeing storage for keys that have gone idle.
        return max(60, math.ceil(config.burst / config.rate) + 1)

    def _build_decision(self, allowed: bool, tokens: float, config: RateLimitConfig) -> Decision:
        remaining = max(0, int(tokens))
        if tokens >= config.burst:
            reset_seconds = 0.0
        else:
            reset_seconds = (config.burst - tokens) / config.rate
        return Decision(
            allowed=allowed,
            limit=int(config.burst),
            remaining=remaining,
            reset_seconds=round(reset_seconds, 6),
        )
