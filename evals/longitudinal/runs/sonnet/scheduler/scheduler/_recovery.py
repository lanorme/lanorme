"""Reconstructing prior run state from a journal, to resume a killed run.

Replaying a journal's events recovers exactly the tasks that reached
:attr:`~scheduler._result.Status.SUCCEEDED` before the process died -- that,
and only that, is what a resumed run skips re-running. A task that was
still pending, still running, waiting out a retry's backoff, or had
permanently failed is *not* assumed safe to skip: the process may have died
mid-attempt with no reliable way to know its true outcome, so those tasks
are simply attempted again from scratch. This is a deliberately
conservative notion of "durable": it never risks treating unfinished work
as done, only ever risks redoing work that (unluckily) had actually
succeeded moments before the kill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ._events import EventType
from ._journal import Journal
from ._result import Status, TaskResult


@dataclass
class ReplayedState:
    """What replaying a journal recovered.

    ``completed`` maps task name to the :class:`TaskResult` it finished
    with, for every task recorded as succeeded. ``next_seq`` is where a
    resumed run's own event numbering should continue from, so its events
    sort after the ones already on disk.
    """

    completed: dict[str, TaskResult] = field(default_factory=dict)
    next_seq: int = 0


def replay(path: str | Path) -> ReplayedState:
    """Read back a journal written by a previous run of the same graph."""
    events = Journal.read_events(path)
    completed: dict[str, TaskResult] = {}
    next_seq = 0
    for event in events:
        next_seq = max(next_seq, event.seq + 1)
        if event.type is EventType.SUCCEEDED:
            completed[event.task] = TaskResult(
                name=event.task,
                status=Status.SUCCEEDED,
                attempts=event.attempt,
                resumed=True,
            )
        elif event.type in (EventType.FAILED, EventType.SKIPPED):
            # A task may be retried across a resume, so a prior terminal
            # failure or skip must not carry over -- only success does.
            completed.pop(event.task, None)
    return ReplayedState(completed=completed, next_seq=next_seq)
