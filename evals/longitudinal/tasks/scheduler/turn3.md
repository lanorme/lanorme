# scheduler, turn 3

Extend the existing `scheduler/` package in this directory. Do not rewrite it.

Add durability and observability.

- Persist run state so a scheduler killed mid-run resumes without re-running
  tasks that already succeeded.
- Structured event log: one record per task state transition, enough to
  reconstruct the run afterwards.
- A run summary: critical path, total queue time versus run time per task, and
  which resource was the bottleneck.
- Expose progress while a run is in flight, safely readable from another
  thread.
