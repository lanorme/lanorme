"""Post-run analysis: critical path, queue time versus run time, and which
resource -- if any -- was the run's bottleneck.

The critical path is computed the classical (CPM/PERT) way: over the
dependency DAG, treating each task's actual execution time (``run_time`` --
time spent inside the task's callable, summed across attempts, excluding
both queueing and retry backoff) as its weight, the critical path is the
longest weighted path through the graph. It names the chain of tasks whose
combined run time set the floor on how fast the whole run could possibly
have finished, no matter how many workers or how much of each resource were
available -- which is usually the more useful reading of "critical path"
than the literal wall-clock timeline of one particular run, since that
timeline is also shaped by incidental contention.

A task recovered from a previous run via :func:`~scheduler._recovery.replay`
(see ``resume`` on :meth:`~scheduler._scheduler.Scheduler.run`) contributes
zero run time here: its real execution happened in a prior process and is
not reconstructed. The critical path for a resumed run therefore reflects
only the work actually executed in *this* process.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ._resources import ResourceUsage
from ._result import TaskResult


@dataclass
class RunSummary:
    """A post-run report, built once when a run finishes."""

    critical_path: list[str] = field(default_factory=list)
    critical_path_time: float = 0.0
    queue_time: dict[str, float] = field(default_factory=dict)
    run_time: dict[str, float] = field(default_factory=dict)
    bottleneck_resource: str | None = None
    resource_usage: dict[str, ResourceUsage] = field(default_factory=dict)


def build_summary(
    depends_on: Mapping[str, tuple[str, ...]],
    results: Mapping[str, TaskResult],
    resource_usage: Mapping[str, ResourceUsage],
) -> RunSummary:
    """Build a :class:`RunSummary` from a finished run's bookkeeping.

    ``depends_on`` maps each task name to the names it depends on;
    ``results`` and ``resource_usage`` are the run's own per-task results
    and per-resource utilisation.
    """
    critical_path, critical_path_time = _critical_path(depends_on, results)
    return RunSummary(
        critical_path=critical_path,
        critical_path_time=critical_path_time,
        queue_time={name: r.queue_time for name, r in results.items()},
        run_time={name: r.run_time for name, r in results.items()},
        bottleneck_resource=_bottleneck(resource_usage),
        resource_usage=dict(resource_usage),
    )


def _bottleneck(resource_usage: Mapping[str, ResourceUsage]) -> str | None:
    """The resource with the highest utilisation over the run, or ``None``
    if the run declared no resources at all."""
    if not resource_usage:
        return None
    busiest = max(
        resource_usage.values(),
        key=lambda usage: (usage.utilization, usage.resource),
    )
    return busiest.resource


def _critical_path(
    depends_on: Mapping[str, tuple[str, ...]], results: Mapping[str, TaskResult]
) -> tuple[list[str], float]:
    """The longest path by cumulative ``run_time`` through the dependency
    DAG, and its total.

    Iterative (memoised over an explicit stack, in the style of
    ``_graph.find_cycle``) so it does not depend on Python's recursion
    limit for a deep graph. The graph is assumed acyclic, as
    :meth:`~scheduler._scheduler.Scheduler.validate` already guarantees for
    any graph a run was actually built from.
    """
    longest: dict[str, float] = {}
    best_pred: dict[str, str | None] = {}

    def weight(name: str) -> float:
        result = results.get(name)
        return result.run_time if result is not None else 0.0

    for start in depends_on:
        if start in longest:
            continue
        stack = [start]
        while stack:
            name = stack[-1]
            if name in longest:
                stack.pop()
                continue
            deps = depends_on.get(name, ())
            unresolved = [dep for dep in deps if dep not in longest]
            if unresolved:
                stack.extend(unresolved)
                continue

            best: str | None = None
            best_value = 0.0
            for dep in deps:
                value = longest[dep]
                if best is None or value > best_value:
                    best, best_value = dep, value
            longest[name] = weight(name) + best_value
            best_pred[name] = best
            stack.pop()

    if not longest:
        return [], 0.0

    end = max(longest, key=lambda name: (longest[name], name))
    path = [end]
    while best_pred.get(path[-1]) is not None:
        path.append(best_pred[path[-1]])  # type: ignore[arg-type]
    path.reverse()
    return path, longest[end]
