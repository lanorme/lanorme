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
