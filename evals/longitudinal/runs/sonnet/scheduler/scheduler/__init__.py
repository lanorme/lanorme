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

Tasks can also compete for a pool of named, numeric resources, and be given
a priority for when several are ready to run at once::

    sched = Scheduler(max_workers=8, resources={"gpu": 2, "memory_gb": 32})
    sched.add_task(
        "train", train_model, resources={"gpu": 1, "memory_gb": 8}, priority=10
    )

A task only starts once its declared ``resources`` are free in the pool, and
gives them back the moment it finishes. Among several ready tasks, the
highest ``priority`` goes first (ties broken by declaration order), though a
lower-priority task that currently fits is started ahead of a higher-priority
one that doesn't, so one task's resource wait never stalls unrelated work.
:meth:`Scheduler.validate` (and so :meth:`Scheduler.run`) raises
:class:`UnsatisfiableResourceError` up front for a task whose requirement
could never be met by the pool, rather than letting the run hang. Each
:class:`RunResult` reports per-resource utilisation over the run in
``resource_usage``.
"""

from __future__ import annotations

from ._errors import (
    CycleError,
    DuplicateTaskError,
    ResourceDeadlockError,
    RunAlreadyStartedError,
    RunCancelledError,
    SchedulerError,
    UnknownDependencyError,
    UnsatisfiableResourceError,
)
from ._resources import ResourcePool, ResourceUsage
from ._result import RunResult, Status, TaskResult
from ._scheduler import Run, Scheduler
from ._task import Backoff, Task, TaskContext

__all__ = [
    "Backoff",
    "CycleError",
    "DuplicateTaskError",
    "ResourceDeadlockError",
    "ResourcePool",
    "ResourceUsage",
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
    "UnsatisfiableResourceError",
]

__version__ = "0.2.0"
