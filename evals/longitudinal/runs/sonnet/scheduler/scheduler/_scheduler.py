"""The scheduler itself: declaring tasks and running the DAG.

Orchestration lives in :class:`Run`. A single :class:`threading.RLock` guards
all of a run's mutable bookkeeping (per-task status, remaining-dependency
counts, pending retry timers, the resource pool, the ready queue); task
callables themselves execute outside the lock, on a bounded
:class:`~concurrent.futures.ThreadPoolExecutor`. Retries wait out their
backoff on a :class:`threading.Timer` rather than by sleeping inside a worker
thread, so a task waiting to retry does not occupy one of the limited worker
slots (or hold any resources).

A task becoming ready (all dependencies satisfied) no longer means it starts
immediately: it is pushed onto a priority queue, and a dispatch pass -- run
after every state change -- admits ready tasks in priority order (ties broken
by declaration order) as long as a worker slot and their declared resources
are both free. A lower-priority task that fits is admitted ahead of a
higher-priority one that doesn't (backfill), so resource contention on one
task never stalls unrelated work.
"""

from __future__ import annotations

import heapq
import threading
import time
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor

from ._errors import (
    CycleError,
    DuplicateTaskError,
    ResourceDeadlockError,
    RunAlreadyStartedError,
    RunCancelledError,
    UnknownDependencyError,
    UnsatisfiableResourceError,
)
from ._graph import find_cycle
from ._resources import Resources, ResourcePool
from ._result import TERMINAL_STATUSES, RunResult, Status, TaskResult
from ._task import Backoff, Task, TaskContext, call_task


class Scheduler:
    """Declares tasks and starts runs of the resulting DAG."""

    def __init__(self, max_workers: int = 4, resources: Resources | None = None) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self._max_workers = max_workers
        self._resources: dict[str, float] = dict(resources or {})
        self._tasks: dict[str, Task] = {}

    def add_task(
        self,
        name: str,
        fn,
        *,
        depends_on: Iterable[str] = (),
        max_retries: int = 0,
        backoff: Backoff | None = None,
        resources: Resources | None = None,
        priority: int = 0,
    ) -> Task:
        """Declare a task. Order does not matter for dependencies: they may
        be declared before or after the tasks that depend on them, since the
        graph is only validated when a run starts. Declaration order *does*
        matter for ``priority``: it is the deterministic tiebreak between
        tasks that end up with equal priority."""
        if name in self._tasks:
            raise DuplicateTaskError(name)
        task = Task(
            name=name,
            fn=fn,
            depends_on=tuple(depends_on),
            max_retries=max_retries,
            backoff=backoff if backoff is not None else Backoff(),
            resources=dict(resources or {}),
            priority=priority,
        )
        self._tasks[name] = task
        return task

    def validate(self) -> None:
        """Check the declared graph for unknown dependencies, cycles, and
        resource requirements that could never be satisfied.

        Raises :class:`UnknownDependencyError`, :class:`CycleError`, or
        :class:`UnsatisfiableResourceError`. Called automatically by
        :meth:`run`; exposed so callers can check a graph without starting
        it.
        """
        for task in self._tasks.values():
            for dep in task.depends_on:
                if dep not in self._tasks:
                    raise UnknownDependencyError(task.name, dep)

        deps_of = {name: task.depends_on for name, task in self._tasks.items()}
        cycle = find_cycle(self._tasks.keys(), deps_of)
        if cycle is not None:
            raise CycleError(cycle)

        pool = ResourcePool(self._resources)
        for task in self._tasks.values():
            unsatisfiable = pool.unsatisfiable(task.resources)
            if unsatisfiable is not None:
                resource, requested, capacity = unsatisfiable
                raise UnsatisfiableResourceError(task.name, resource, requested, capacity)

    def run(self) -> Run:
        """Validate the graph and start a run. Returns immediately with a
        :class:`Run` handle; call :meth:`Run.wait` to block for the result
        or :meth:`Run.cancel` to stop it early."""
        self.validate()
        run = Run(dict(self._tasks), self._max_workers, self._resources)
        run._start()
        return run


class Run:
    """A single, in-progress (or finished) execution of a task graph.

    Do not construct directly; obtain one from :meth:`Scheduler.run`.
    """

    def __init__(
        self, tasks: dict[str, Task], max_workers: int, resources: Mapping[str, float]
    ) -> None:
        self._tasks = tasks
        self._dependents: dict[str, list[str]] = {name: [] for name in tasks}
        for name, task in tasks.items():
            for dep in task.depends_on:
                self._dependents[dep].append(name)

        self._remaining_deps = {
            name: len(task.depends_on) for name, task in tasks.items()
        }
        self._attempts = {name: 0 for name in tasks}
        self._results = {name: TaskResult(name=name) for name in tasks}
        self._remaining_count = len(tasks)
        self._sequence = {name: i for i, name in enumerate(tasks)}

        self._max_workers = max_workers
        self._active_workers = 0
        # Min-heap of (-priority, sequence, name): higher priority first,
        # ties broken by declaration order.
        self._ready_heap: list[tuple[int, int, str]] = []
        self._pool = ResourcePool(resources)

        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self._done_event = threading.Event()
        self._pending_retries: dict[str, threading.Timer] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

        self._started_at = 0.0
        self._ended_at = 0.0
        self._run_result: RunResult | None = None
        self._start_lock = threading.Lock()
        self._started = False

    # -- public API --------------------------------------------------

    def cancel(self) -> None:
        """Stop the run: nothing new starts.

        Tasks already executing keep running to completion in their worker
        thread (Python cannot forcibly interrupt a running thread); a task
        can interrupt itself early by checking
        ``ctx.cancel_event.is_set()``. Tasks waiting on a retry's backoff
        delay are abandoned immediately and finalised as failed. Tasks that
        never started are finalised as skipped.
        """
        self._cancel_event.set()
        with self._lock:
            for name, timer in list(self._pending_retries.items()):
                timer.cancel()
                del self._pending_retries[name]
                self._finalize_failed(name, RunCancelledError(name))

            for name, result in self._results.items():
                if result.status is Status.PENDING:
                    self._finalize_skipped(name)

            self._maybe_finish_locked()

    def wait(self, timeout: float | None = None) -> RunResult:
        """Block until the run finishes, then return its :class:`RunResult`.

        Raises :class:`TimeoutError` if ``timeout`` elapses first; the run
        keeps going in the background and ``wait`` may be called again.
        """
        finished = self._done_event.wait(timeout)
        if not finished:
            raise TimeoutError("run did not finish within the given timeout")
        assert self._run_result is not None
        return self._run_result

    @property
    def done(self) -> bool:
        return self._done_event.is_set()

    # -- orchestration -------------------------------------------------

    def _start(self) -> None:
        with self._start_lock:
            if self._started:
                raise RunAlreadyStartedError("this run has already been started")
            self._started = True

        self._started_at = time.monotonic()
        self._pool.begin(self._started_at)
        if not self._tasks:
            self._finish()
            return

        with self._lock:
            for name, count in self._remaining_deps.items():
                if count == 0:
                    self._push_ready(name)
            to_submit = self._dispatch_locked()
            self._maybe_finish_locked()
        for name in to_submit:
            self._submit(name)

    def _mark_running(self, name: str) -> None:
        """Must be called with ``self._lock`` held.

        Idempotent on ``started_at``: a retried task is dispatched again
        (its resources and worker slot were given back for the backoff
        wait), but its ``started_at`` -- and so its reported ``duration`` --
        should still span from its very first attempt, not reset on each
        retry.
        """
        result = self._results[name]
        result.status = Status.RUNNING
        if result.started_at is None:
            result.started_at = time.monotonic()

    def _push_ready(self, name: str) -> None:
        """Add ``name`` to the ready queue. Must be called with ``self._lock``
        held. Does not mark it running: :meth:`_dispatch_locked` does that
        only once a worker slot and its resources are actually granted."""
        task = self._tasks[name]
        heapq.heappush(self._ready_heap, (-task.priority, self._sequence[name], name))

    def _dispatch_locked(self) -> list[str]:
        """Admit as many ready tasks as fit, in priority order.

        A task is skipped over (not admitted, but left ready) if its
        resources aren't currently free, so a lower-priority task that fits
        can still start (backfill) rather than the run stalling behind one
        that doesn't. Must be called with ``self._lock`` held; returns the
        names to hand to the executor once the lock is released.
        """
        admitted: list[str] = []
        deferred: list[tuple[int, int, str]] = []
        while self._ready_heap and self._active_workers < self._max_workers:
            entry = heapq.heappop(self._ready_heap)
            name = entry[2]
            if self._results[name].status in TERMINAL_STATUSES:
                # Already finalised elsewhere (e.g. cancel()); drop it. A
                # freshly-ready task is PENDING; a retried one is RUNNING
                # (see _mark_running) -- both are still admissible here.
                continue
            task = self._tasks[name]
            if self._pool.fits(task.resources):
                self._pool.acquire(task.resources)
                self._active_workers += 1
                self._mark_running(name)
                admitted.append(name)
            else:
                deferred.append(entry)
        for entry in deferred:
            heapq.heappush(self._ready_heap, entry)

        if not admitted and self._ready_heap and self._is_stuck_locked():
            self._resolve_deadlock_locked()
        return admitted

    def _is_stuck_locked(self) -> bool:
        """Whether nothing left running or retrying could ever free the
        resources the remaining ready tasks are waiting on.

        Must be called with ``self._lock`` held. This should be unreachable
        in practice: :meth:`Scheduler.validate` already refuses any task
        whose requirement exceeds the pool's total capacity, which is the
        only way a resource wait can never resolve. It stays as a runtime
        safety net.
        """
        return self._active_workers == 0 and not self._pending_retries

    def _resolve_deadlock_locked(self) -> None:
        """Fail every stuck ready task (and its descendants) instead of
        leaving the run to hang forever. Must be called with ``self._lock``
        held."""
        stuck = [entry[2] for entry in self._ready_heap]
        self._ready_heap.clear()
        for name in stuck:
            self._finalize_failed(name, ResourceDeadlockError(name))

    def _submit(self, name: str) -> None:
        self._executor.submit(self._run_one, name)

    def _run_one(self, name: str) -> None:
        with self._lock:
            self._attempts[name] += 1
            attempt = self._attempts[name]
        ctx = TaskContext(name=name, attempt=attempt, cancel_event=self._cancel_event)
        task = self._tasks[name]
        try:
            value = call_task(task.fn, ctx)
        except Exception as exc:  # noqa: BLE001 - a task's own failure
            self._on_failure(name, exc)
        else:
            self._on_success(name, value)

    def _release(self, name: str) -> None:
        """Return a finished attempt's resources and worker slot to the
        pool. Must be called with ``self._lock`` held, exactly once per
        attempt that was admitted by :meth:`_dispatch_locked`."""
        self._pool.release(self._tasks[name].resources)
        self._active_workers -= 1

    def _on_success(self, name: str, value) -> None:
        to_submit: list[str] = []
        with self._lock:
            self._release(name)
            result = self._results[name]
            result.status = Status.SUCCEEDED
            result.ended_at = time.monotonic()
            result.value = value
            self._remaining_count -= 1

            for dependent in self._dependents[name]:
                self._remaining_deps[dependent] -= 1
                if (
                    self._remaining_deps[dependent] == 0
                    and self._results[dependent].status is Status.PENDING
                    and not self._cancel_event.is_set()
                ):
                    self._push_ready(dependent)

            to_submit = self._dispatch_locked()
            self._maybe_finish_locked()

        for dependent in to_submit:
            self._submit(dependent)

    def _on_failure(self, name: str, exc: Exception) -> None:
        to_submit: list[str] = []
        with self._lock:
            self._release(name)
            task = self._tasks[name]
            attempt = self._attempts[name]
            result = self._results[name]
            result.error = exc

            retries_left = attempt <= task.max_retries
            if retries_left and not self._cancel_event.is_set():
                delay = task.backoff.delay_for(attempt)
                timer = threading.Timer(delay, self._retry, args=(name,))
                timer.daemon = True
                self._pending_retries[name] = timer
            else:
                timer = None
                self._finalize_failed(name, exc)

            to_submit = self._dispatch_locked()
            self._maybe_finish_locked()

        if timer is not None:
            timer.start()
        for dependent in to_submit:
            self._submit(dependent)

    def _retry(self, name: str) -> None:
        to_submit: list[str] = []
        with self._lock:
            self._pending_retries.pop(name, None)
            if self._cancel_event.is_set():
                self._finalize_failed(name, RunCancelledError(name))
                self._maybe_finish_locked()
                return
            self._push_ready(name)
            to_submit = self._dispatch_locked()
            self._maybe_finish_locked()
        for dependent in to_submit:
            self._submit(dependent)

    def _finalize_failed(self, name: str, exc: BaseException) -> None:
        """Must be called with ``self._lock`` held."""
        result = self._results[name]
        if result.status in TERMINAL_STATUSES:
            return
        result.status = Status.FAILED
        result.error = exc
        result.attempts = self._attempts[name]
        result.ended_at = time.monotonic()
        if result.started_at is None:
            result.started_at = result.ended_at
        self._remaining_count -= 1
        self._cascade_skip(name)

    def _finalize_skipped(self, name: str) -> None:
        """Must be called with ``self._lock`` held."""
        result = self._results[name]
        if result.status in TERMINAL_STATUSES:
            return
        now = time.monotonic()
        result.status = Status.SKIPPED
        result.started_at = result.started_at or now
        result.ended_at = now
        self._remaining_count -= 1

    def _cascade_skip(self, name: str) -> None:
        """Skip every descendant of ``name`` that hasn't started.

        Must be called with ``self._lock`` held.
        """
        stack = list(self._dependents.get(name, ()))
        while stack:
            dependent = stack.pop()
            result = self._results[dependent]
            if result.status in TERMINAL_STATUSES:
                continue
            self._finalize_skipped(dependent)
            stack.extend(self._dependents.get(dependent, ()))

    def _maybe_finish_locked(self) -> None:
        """Must be called with ``self._lock`` held."""
        if self._remaining_count == 0 and not self._done_event.is_set():
            self._finish()

    def _finish(self) -> None:
        self._ended_at = time.monotonic()
        # Attempts weren't recorded for successful/skipped tasks above;
        # fill them in uniformly now.
        for name, result in self._results.items():
            if result.attempts == 0:
                result.attempts = self._attempts.get(name, 0)
        run_duration = self._ended_at - self._started_at
        self._run_result = RunResult(
            results={n: _copy_result(r) for n, r in self._results.items()},
            cancelled=self._cancel_event.is_set(),
            started_at=self._started_at,
            ended_at=self._ended_at,
            resource_usage=self._pool.report(self._ended_at, run_duration),
        )
        self._done_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)


def _copy_result(result: TaskResult) -> TaskResult:
    return TaskResult(
        name=result.name,
        status=result.status,
        attempts=result.attempts,
        started_at=result.started_at,
        ended_at=result.ended_at,
        error=result.error,
        value=result.value,
    )
