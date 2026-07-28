"""Task declarations: what to run, its dependencies, and its retry policy."""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Backoff:
    """An exponential backoff schedule used between retry attempts.

    ``delay_for(1)`` is the wait before the first retry, ``delay_for(2)``
    before the second, and so on. The delay grows as
    ``base * factor ** (retry_number - 1)``, optionally capped at
    ``max_delay``.
    """

    base: float = 0.1
    factor: float = 2.0
    max_delay: float | None = None

    def __post_init__(self) -> None:
        if self.base < 0:
            raise ValueError("backoff base must be >= 0")
        if self.factor < 1:
            raise ValueError("backoff factor must be >= 1")
        if self.max_delay is not None and self.max_delay < 0:
            raise ValueError("backoff max_delay must be >= 0")

    def delay_for(self, retry_number: int) -> float:
        """Seconds to wait before the given retry (1-based)."""
        delay = self.base * (self.factor ** (retry_number - 1))
        if self.max_delay is not None:
            delay = min(delay, self.max_delay)
        return delay


@dataclass(frozen=True)
class TaskContext:
    """Passed to a task's callable if it accepts one positional argument."""

    name: str
    attempt: int  # 1-based: 1 is the first try, 2 the first retry, etc.
    cancel_event: threading.Event


@dataclass(frozen=True)
class Task:
    """A single unit of work in the DAG.

    ``fn`` is called with no arguments, or with a single :class:`TaskContext`
    argument if its signature accepts one -- this lets a task cooperatively
    notice cancellation via ``ctx.cancel_event`` without forcing every task
    to take a parameter it doesn't need.
    """

    name: str
    fn: Callable[..., Any]
    depends_on: tuple[str, ...] = ()
    max_retries: int = 0
    backoff: Backoff = field(default_factory=Backoff)

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")


def call_task(fn: Callable[..., Any], ctx: TaskContext) -> Any:
    """Invoke a task's callable, passing ``ctx`` only if it wants one."""
    if _accepts_context(fn):
        return fn(ctx)
    return fn()


def _accepts_context(fn: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        # Signature isn't introspectable (e.g. some builtins/C callables).
        # Assume it wants the context; that's the more common shape.
        return True

    for param in parameters.values():
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            return True
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
    return False
