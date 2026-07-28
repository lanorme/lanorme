# scheduler, turn 1

Build a DAG task scheduler as a Python package `scheduler/`.

- Declare tasks with dependencies. Detect cycles and refuse them, naming the
  cycle.
- Execute in dependency order, running independent tasks concurrently up to a
  worker limit.
- Retry a failed task with backoff, up to a per-task limit. A task whose
  dependency failed permanently is skipped, not run.
- A run produces a result per task: succeeded, failed, skipped, with timings.
- Cancel a run: in-flight tasks finish or are interrupted, nothing new starts.

Standard library only, threads rather than asyncio.
