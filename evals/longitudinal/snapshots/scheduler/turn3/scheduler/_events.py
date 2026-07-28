"""Structured events: one record per task state transition.

Every transition a task goes through during a run -- becoming eligible to
run, starting an attempt, succeeding, an attempt failing, being scheduled
for retry, being finalised as failed, being skipped, or being recovered
from a previous run on resume -- is recorded as an :class:`Event`. Collected
in order, the events are enough to reconstruct a run's whole timeline
afterwards: which tasks ran when, how many attempts each took, and why any
failed or were skipped.

Events are plain, JSON-serialisable data (:meth:`Event.to_dict` and
:meth:`Event.from_dict`), so they can be appended to a durable log (see
``_journal.py``) and read back later, including by a different process
entirely.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any


class EventType(enum.Enum):
    """The kind of state transition an :class:`Event` records."""

    QUEUED = "queued"
    STARTED = "started"
    SUCCEEDED = "succeeded"
    ATTEMPT_FAILED = "attempt_failed"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    SKIPPED = "skipped"
    RESUMED = "resumed"


@dataclass(frozen=True)
class Event:
    """One task state transition.

    ``seq`` is a strictly increasing, run-scoped sequence number and is the
    authoritative ordering -- two events can share a ``time`` reading on a
    fast machine or a coarse clock. ``time`` is a wall-clock (``time.time()``)
    reading rather than a monotonic one, because it must stay meaningful
    across a process restart: that is what lets a persisted log be replayed
    to resume a run after the process that wrote it is gone.
    """

    seq: int
    time: float
    task: str
    type: EventType
    attempt: int = 0
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe representation, used by the journal writer."""
        return {
            "seq": self.seq,
            "time": self.time,
            "task": self.task,
            "type": self.type.value,
            "attempt": self.attempt,
            "error": self.error,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        """Inverse of :meth:`to_dict`, used when replaying a journal."""
        return cls(
            seq=data["seq"],
            time=data["time"],
            task=data["task"],
            type=EventType(data["type"]),
            attempt=data.get("attempt", 0),
            error=data.get("error"),
            extra=dict(data.get("extra") or {}),
        )


def wall_clock() -> float:
    """The current wall-clock time for a new event.

    A thin wrapper around ``time.time()`` so there is a single seam to
    substitute in tests that need deterministic timestamps.
    """
    return time.time()
