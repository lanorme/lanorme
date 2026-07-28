"""Per-task and per-run results, including status and timings."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ._resources import ResourceUsage

if TYPE_CHECKING:
    # Deferred: `_summary` and `_events` would otherwise import back from
    # here (RunSummary/Event are typed on RunResult below), so these names
    # are only ever needed for type annotations, never at runtime -- thanks
    # to `from __future__ import annotations`, the annotations themselves
    # are never evaluated either.
    from ._events import Event
    from ._summary import RunSummary


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

    ``queue_time`` is the cumulative time the task spent ready to run
    (dependencies satisfied) but waiting for a free worker slot or its
    resources, across every attempt. ``run_time`` is the cumulative time
    actually spent inside the task's callable, across every attempt --
    unlike ``duration``, it excludes both queueing and time spent waiting
    out a retry's backoff delay, so ``queue_time + run_time <= duration``
    (the remainder, if any, was backoff wait).

    ``resumed`` is ``True`` for a task recovered from a previous, killed
    run's journal rather than executed in this process (see ``resume`` on
    :meth:`~scheduler._scheduler.Scheduler.run`) -- such a task's
    ``started_at``/``ended_at`` mark this run's start (so ``duration`` is
    0), its ``value`` is unavailable (a task's return value is not
    persisted), and ``queue_time``/``run_time`` are 0 since neither
    happened in this process.
    """

    name: str
    status: Status = Status.PENDING
    attempts: int = 0
    started_at: float | None = None
    ended_at: float | None = None
    error: BaseException | None = None
    value: Any = None
    queue_time: float = 0.0
    run_time: float = 0.0
    resumed: bool = False

    @property
    def duration(self) -> float | None:
        if self.started_at is None or self.ended_at is None:
            return None
        return self.ended_at - self.started_at


@dataclass
class RunResult:
    """The final outcome of a run: one :class:`TaskResult` per task, plus
    how busy each declared resource was over the run (``resource_usage``,
    keyed by resource name; empty if the run declared no resource pool).

    ``events`` is the complete, ordered structured log of every task state
    transition the run went through -- enough, on its own, to reconstruct
    the run's timeline afterwards. ``summary`` is the derived report built
    from it and from ``resource_usage``: the critical path, queue versus
    run time per task, and which resource (if any) was the bottleneck.
    """

    results: dict[str, TaskResult] = field(default_factory=dict)
    cancelled: bool = False
    started_at: float = 0.0
    ended_at: float = 0.0
    resource_usage: dict[str, ResourceUsage] = field(default_factory=dict)
    events: "list[Event]" = field(default_factory=list)
    summary: "RunSummary | None" = None

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
