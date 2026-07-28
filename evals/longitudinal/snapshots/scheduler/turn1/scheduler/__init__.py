"""A small, dependency-free DAG task scheduler.

Declare tasks with dependencies, then run them concurrently -- in
dependency order, up to a worker limit -- on a thread pool. A task that
fails is retried with backoff up to its own limit; a task whose dependency
failed permanently is skipped rather than run. A run can be cancelled: no
new task starts, and tasks already executing finish (or cooperatively
notice the cancellation and interrupt themselves).

Standard library only, threads rather than asyncio.

Example::

    from scheduler import Scheduler

    sched = Scheduler(max_workers=4)
    sched.add_task("fetch", fetch_data)
    sched.add_task("clean", clean_data, depends_on=["fetch"])
    sched.add_task("report", build_report, depends_on=["clean"], max_retries=2)

    run = sched.run()
    result = run.wait()
    for name, task_result in result.results.items():
        print(name, task_result.status, task_result.duration)

A task's callable is invoked with no arguments, or with a single
:class:`TaskContext` argument if its signature accepts one -- use
``ctx.cancel_event`` to check for cancellation from inside a long task::

    def fetch_data(ctx):
        for chunk in stream():
            if ctx.cancel_event.is_set():
                raise CancelledByUser()
            process(chunk)

To cancel a run in progress, call ``run.cancel()`` from any thread.
"""

from __future__ import annotations

from ._errors import (
    CycleError,
    DuplicateTaskError,
    RunAlreadyStartedError,
    RunCancelledError,
    SchedulerError,
    UnknownDependencyError,
)
from ._result import RunResult, Status, TaskResult
from ._scheduler import Run, Scheduler
from ._task import Backoff, Task, TaskContext

__all__ = [
    "Backoff",
    "CycleError",
    "DuplicateTaskError",
    "Run",
    "RunAlreadyStartedError",
    "RunCancelledError",
    "RunResult",
    "Scheduler",
    "SchedulerError",
    "Status",
    "Task",
    "TaskContext",
    "TaskResult",
    "UnknownDependencyError",
]

__version__ = "0.1.0"
