"""Exceptions raised by the scheduler package."""

from __future__ import annotations


class SchedulerError(Exception):
    """Base class for all errors raised by this package."""


class DuplicateTaskError(SchedulerError):
    """Raised when a task name is declared more than once."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"task {name!r} is already declared")


class UnknownDependencyError(SchedulerError):
    """Raised when a task depends on a name that was never declared."""

    def __init__(self, task_name: str, dependency_name: str) -> None:
        self.task_name = task_name
        self.dependency_name = dependency_name
        super().__init__(
            f"task {task_name!r} depends on {dependency_name!r}, "
            f"which was never declared"
        )


class CycleError(SchedulerError):
    """Raised when the task graph contains a dependency cycle.

    ``cycle`` names the offending tasks in order, starting and ending on the
    same task, e.g. ``["a", "b", "c", "a"]`` for a cycle a -> b -> c -> a.
    """

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = list(cycle)
        path = " -> ".join(self.cycle)
        super().__init__(f"dependency cycle detected: {path}")


class UnsatisfiableResourceError(SchedulerError):
    """Raised when a task's resource requirement can never be met.

    A task's requirement for a named resource must fit within the run's
    total pool capacity for that resource -- if it doesn't, no amount of
    waiting for other tasks to finish will ever free enough of it, so the
    run is refused up front rather than left to hang.
    """

    def __init__(self, task_name: str, resource: str, requested: float, capacity: float) -> None:
        self.task_name = task_name
        self.resource = resource
        self.requested = requested
        self.capacity = capacity
        super().__init__(
            f"task {task_name!r} requires {requested!r} of resource "
            f"{resource!r}, but the pool only ever has {capacity!r} of it -- "
            "this task could never run"
        )


class ResourceDeadlockError(SchedulerError):
    """Recorded as a task's error if the run stalls on resources at runtime.

    :meth:`Scheduler.validate` already refuses a graph where some task's
    requirement exceeds the pool's total capacity, which is the only way a
    resource wait can never resolve -- so this should not occur in practice.
    It exists as a safety net: if every runnable task is nonetheless stuck
    wanting resources that nothing in flight can ever free, the affected
    tasks are failed with this error (and their descendants skipped) instead
    of leaving the run to hang forever.
    """

    def __init__(self, task_name: str) -> None:
        self.task_name = task_name
        super().__init__(
            f"task {task_name!r} is ready but can never acquire its "
            "resources -- no task is running that could free them"
        )


class RunAlreadyStartedError(SchedulerError):
    """Raised when a run is started more than once."""


class RunCancelledError(SchedulerError):
    """Recorded as a task's error when a scheduled retry is cancelled.

    This happens when :meth:`Run.cancel` is called while a task is waiting
    out its backoff delay before a retry: the retry is abandoned rather than
    started, and the task is finalised as failed with this as its error.
    """

    def __init__(self, task_name: str) -> None:
        self.task_name = task_name
        super().__init__(f"task {task_name!r} cancelled before its retry ran")
