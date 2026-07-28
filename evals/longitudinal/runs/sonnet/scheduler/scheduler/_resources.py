"""A resource pool tasks draw from: named, numeric, finite capacities.

A run is given a pool of named resources (for example ``{"gpu": 2,
"memory_gb": 32}``). A task declares how much of each it needs to run (for
example ``{"gpu": 1, "memory_gb": 8}``); it is only admitted once that much of
every named resource is free, and the amount is returned to the pool the
moment the task's attempt ends (success, failure, or abandonment before a
retry -- never while it merely waits out a backoff delay).

:class:`ResourcePool` is not thread-safe on its own: every method here is
called by :class:`scheduler._scheduler.Run` with its run-wide lock already
held, so the pool itself does not need one.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass

Resources = Mapping[str, float]

# Tolerance for floating-point requirement/capacity comparisons.
_EPSILON = 1e-9


@dataclass(frozen=True)
class ResourceSnapshot:
    """An instantaneous (not time-integrated) read of one resource's usage,
    for progress reporting while a run is still in flight."""

    resource: str
    used: float
    capacity: float

    @property
    def free(self) -> float:
        return self.capacity - self.used


@dataclass
class ResourceUsage:
    """Utilisation of one named resource over the course of a run.

    ``resource_seconds`` is the time integral of "amount in use", e.g. two
    units held for three seconds contributes 6. ``utilization`` is that
    integral normalised to the pool's capacity and the run's wall time, in
    ``[0, 1]``: 1.0 means the resource was fully occupied for the entire run.
    """

    resource: str
    capacity: float
    peak_used: float = 0.0
    resource_seconds: float = 0.0
    utilization: float = 0.0


class ResourcePool:
    """Tracks how much of each named resource is currently in use.

    Capacities are fixed for the lifetime of a run. A resource name a task
    requests but that was never given any capacity behaves as a resource
    with zero capacity: any positive requirement for it can never be
    satisfied.
    """

    def __init__(self, capacities: Resources) -> None:
        self._capacities: dict[str, float] = dict(capacities)
        self._used: dict[str, float] = dict.fromkeys(self._capacities, 0.0)
        self._peak: dict[str, float] = dict.fromkeys(self._capacities, 0.0)
        self._resource_seconds: dict[str, float] = dict.fromkeys(
            self._capacities, 0.0
        )
        self._last_ts = time.monotonic()

    def begin(self, now: float) -> None:
        """Reset the usage clock to ``now``, the moment a run starts."""
        self._last_ts = now

    def unsatisfiable(self, requirements: Resources) -> tuple[str, float, float] | None:
        """Return ``(resource, requested, capacity)`` for the first
        requirement that exceeds the pool's total capacity -- something that
        could never be satisfied no matter what else is running -- or
        ``None`` if every requirement fits within capacity."""
        for name, amount in requirements.items():
            capacity = self._capacities.get(name, 0.0)
            if amount > capacity + _EPSILON:
                return name, amount, capacity
        return None

    def fits(self, requirements: Resources) -> bool:
        """Whether ``requirements`` can be granted right now."""
        for name, amount in requirements.items():
            if amount <= 0:
                continue
            capacity = self._capacities.get(name, 0.0)
            used = self._used.get(name, 0.0)
            if used + amount > capacity + _EPSILON:
                return False
        return True

    def acquire(self, requirements: Resources) -> None:
        """Grant ``requirements``. Caller must have checked :meth:`fits`."""
        self._advance(time.monotonic())
        for name, amount in requirements.items():
            if amount <= 0:
                continue
            new_used = self._used.get(name, 0.0) + amount
            self._used[name] = new_used
            if new_used > self._peak.get(name, 0.0):
                self._peak[name] = new_used

    def release(self, requirements: Resources) -> None:
        """Return ``requirements`` to the pool."""
        self._advance(time.monotonic())
        for name, amount in requirements.items():
            if amount <= 0:
                continue
            self._used[name] = self._used.get(name, 0.0) - amount

    def _advance(self, now: float) -> None:
        """Fold the time since the last change into the running integral."""
        elapsed = now - self._last_ts
        if elapsed > 0:
            for name, used in self._used.items():
                if used:
                    self._resource_seconds[name] = (
                        self._resource_seconds.get(name, 0.0) + used * elapsed
                    )
        self._last_ts = now

    def report(self, ended_at: float, run_duration: float) -> dict[str, ResourceUsage]:
        """Final per-resource utilisation, as of ``ended_at``.

        Flushes the usage integral up to ``ended_at`` first, so resources
        still held when the run finished are accounted for.
        """
        self._advance(ended_at)
        names = set(self._capacities) | set(self._resource_seconds)
        report: dict[str, ResourceUsage] = {}
        for name in names:
            capacity = self._capacities.get(name, 0.0)
            resource_seconds = self._resource_seconds.get(name, 0.0)
            denom = capacity * run_duration
            utilization = resource_seconds / denom if denom > 0 else 0.0
            report[name] = ResourceUsage(
                resource=name,
                capacity=capacity,
                peak_used=self._peak.get(name, 0.0),
                resource_seconds=resource_seconds,
                utilization=min(utilization, 1.0),
            )
        return report
