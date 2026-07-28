"""A thread-safe, point-in-time snapshot of an in-flight (or finished) run."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._resources import ResourceSnapshot


@dataclass(frozen=True)
class Progress:
    """A snapshot of a :class:`~scheduler._scheduler.Run` at one instant.

    Obtained from :attr:`~scheduler._scheduler.Run.progress`, which builds
    one under the run's own lock and hands back an immutable copy -- safe
    to read from any thread, concurrently with the run's own worker and
    timer threads, while the run is still going or after it has finished.
    """

    total: int
    pending: int
    running: int
    succeeded: int
    failed: int
    skipped: int
    running_tasks: tuple[str, ...] = ()
    elapsed: float = 0.0
    resource_usage: dict[str, ResourceSnapshot] = field(default_factory=dict)

    @property
    def completed(self) -> int:
        """Tasks that have reached a terminal state (succeeded, failed, or
        skipped)."""
        return self.succeeded + self.failed + self.skipped

    @property
    def fraction_done(self) -> float:
        """``completed / total``, in ``[0, 1]``. ``1.0`` for a run with no
        tasks at all."""
        if not self.total:
            return 1.0
        return self.completed / self.total
