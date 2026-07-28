"""Per-task and per-run results, including status and timings."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Status(enum.Enum):
    """A task's state during and after a run.

    Only ``SUCCEEDED``, ``FAILED`` and ``SKIPPED`` are terminal: every task
    ends a completed run in exactly one of those three states. ``PENDING``
    and ``RUNNING`` are transient, in-run states.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


TERMINAL_STATUSES = frozenset({Status.SUCCEEDED, Status.FAILED, Status.SKIPPED})


@dataclass
class TaskResult:
    """The outcome of one task at the end of a run.

    ``started_at``/``ended_at`` are ``time.monotonic()`` readings, so
    ``duration`` is reliable even if the wall clock changes mid-run.
    A skipped task was never run, so its ``started_at``/``ended_at`` mark
    the moment it was determined to be unrunnable, and ``duration`` is 0.
    """

    name: str
    status: Status = Status.PENDING
    attempts: int = 0
    started_at: float | None = None
    ended_at: float | None = None
    error: BaseException | None = None
    value: Any = None

    @property
    def duration(self) -> float | None:
        if self.started_at is None or self.ended_at is None:
            return None
        return self.ended_at - self.started_at


@dataclass
class RunResult:
    """The final outcome of a run: one :class:`TaskResult` per task."""

    results: dict[str, TaskResult] = field(default_factory=dict)
    cancelled: bool = False
    started_at: float = 0.0
    ended_at: float = 0.0

    @property
    def duration(self) -> float:
        return self.ended_at - self.started_at

    @property
    def succeeded(self) -> bool:
        """Whether every task succeeded and the run was never cancelled."""
        if self.cancelled:
            return False
        return all(r.status is Status.SUCCEEDED for r in self.results.values())

    def __getitem__(self, name: str) -> TaskResult:
        return self.results[name]

    def __iter__(self):
        return iter(self.results.values())

    def __len__(self) -> int:
        return len(self.results)

    def by_status(self, status: Status) -> list[TaskResult]:
        return [r for r in self.results.values() if r.status is status]
