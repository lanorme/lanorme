"""The scheduler itself: declaring tasks and running the DAG.

Orchestration lives in :class:`Run`. A single :class:`threading.RLock` guards
all of a run's mutable bookkeeping (per-task status, remaining-dependency
counts, pending retry timers); task callables themselves execute outside the
lock, on a bounded :class:`~concurrent.futures.ThreadPoolExecutor`. Retries
wait out their backoff on a :class:`threading.Timer` rather than by sleeping
inside a worker thread, so a task waiting to retry does not occupy one of the
limited worker slots.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from ._errors import (
    CycleError,
    DuplicateTaskError,
    RunAlreadyStartedError,
    RunCancelledError,
    UnknownDependencyError,
)
from ._graph import find_cycle
from ._result import TERMINAL_STATUSES, RunResult, Status, TaskResult
from ._task import Backoff, Task, TaskContext, call_task


class Scheduler:
    """Declares tasks and starts runs of the resulting DAG."""

    def __init__(self, max_workers: int = 4) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self._max_workers = max_workers
        self._tasks: dict[str, Task] = {}

    def add_task(
        self,
        name: str,
        fn,
        *,
        depends_on: Iterable[str] = (),
        max_retries: int = 0,
        backoff: Backoff | None = None,
    ) -> Task:
        """Declare a task. Order does not matter: dependencies may be
        declared before or after the tasks that depend on them, since the
        graph is only validated when a run starts."""
        if name in self._tasks:
            raise DuplicateTaskError(name)
        task = Task(
            name=name,
            fn=fn,
            depends_on=tuple(depends_on),
            max_retries=max_retries,
            backoff=backoff if backoff is not None else Backoff(),
        )
        self._tasks[name] = task
        return task

    def validate(self) -> None:
        """Check the declared graph for unknown dependencies and cycles.

        Raises :class:`UnknownDependencyError` or :class:`CycleError`. Called
        automatically by :meth:`run`; exposed so callers can check a graph
        without starting it.
        """
        for task in self._tasks.values():
            for dep in task.depends_on:
                if dep not in self._tasks:
                    raise UnknownDependencyError(task.name, dep)

        deps_of = {name: task.depends_on for name, task in self._tasks.items()}
        cycle = find_cycle(self._tasks.keys(), deps_of)
        if cycle is not None:
            raise CycleError(cycle)

    def run(self) -> Run:
        """Validate the graph and start a run. Returns immediately with a
        :class:`Run` handle; call :meth:`Run.wait` to block for the result
        or :meth:`Run.cancel` to stop it early."""
        self.validate()
        run = Run(dict(self._tasks), self._max_workers)
        run._start()
        return run


class Run:
    """A single, in-progress (or finished) execution of a task graph.

    Do not construct directly; obtain one from :meth:`Scheduler.run`.
    """

    def __init__(self, tasks: dict[str, Task], max_workers: int) -> None:
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
        if not self._tasks:
            self._finish()
            return

        with self._lock:
            ready = [
                name for name, count in self._remaining_deps.items() if count == 0
            ]
            for name in ready:
                self._mark_running(name)
        for name in ready:
            self._submit(name)

    def _mark_running(self, name: str) -> None:
        """Must be called with ``self._lock`` held."""
        result = self._results[name]
        result.status = Status.RUNNING
        result.started_at = time.monotonic()

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

    def _on_success(self, name: str, value) -> None:
        to_submit: list[str] = []
        with self._lock:
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
                    self._mark_running(dependent)
                    to_submit.append(dependent)

            self._maybe_finish_locked()

        for dependent in to_submit:
            self._submit(dependent)

    def _on_failure(self, name: str, exc: Exception) -> None:
        with self._lock:
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
                self._maybe_finish_locked()

        if timer is not None:
            timer.start()

    def _retry(self, name: str) -> None:
        with self._lock:
            self._pending_retries.pop(name, None)
            if self._cancel_event.is_set():
                self._finalize_failed(name, RunCancelledError(name))
                self._maybe_finish_locked()
                return
        self._submit(name)

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
        self._run_result = RunResult(
            results={n: _copy_result(r) for n, r in self._results.items()},
            cancelled=self._cancel_event.is_set(),
            started_at=self._started_at,
            ended_at=self._ended_at,
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
