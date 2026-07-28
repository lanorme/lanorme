# scheduler, turn 2

Extend the existing `scheduler/` package in this directory. Do not rewrite it.

Add resources and priority.

- Tasks declare resource requirements (for example `{"gpu": 1, "memory_gb": 8}`)
  against a pool the run is given. A task waits until its resources are free.
- Priorities decide which of several ready tasks runs first, with ties broken
  deterministically.
- Detect the case where no runnable task can ever get its resources, and fail
  the run with an explanation rather than hanging.
- Report resource utilisation over the run.
