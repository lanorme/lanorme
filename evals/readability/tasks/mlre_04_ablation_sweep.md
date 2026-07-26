# mlre_04_ablation_sweep

Write `sweep.py`, the driver for a hyperparameter ablation.

It should:

- Take a sweep spec in YAML-ish form (parse it yourself from JSON to avoid the
  dependency): a base config plus axes to vary, supporting a full grid and a
  random-search mode with a budget.
- Expand the axes into concrete run configs, skipping combinations excluded by
  a constraint expression (for example, batch size times sequence length must
  stay under a token budget).
- Launch runs as subprocesses with a bounded worker pool, one GPU per run,
  respecting `CUDA_VISIBLE_DEVICES`.
- Stream each run's JSONL metrics, parse the final eval loss, and handle a run
  that dies (non-zero exit, OOM in stderr, or no metrics produced) by recording
  the failure rather than crashing the sweep.
- Support resume: on restart, skip runs that already completed, re-queue the
  failures.
- Aggregate results into a table: per-axis marginal means, the best run per
  axis value, and a scaling-law style fit of loss against compute
  (`L = a * C ** -b + c`) with the fitted parameters reported.
- Write the aggregate as both JSON and Markdown.

NumPy and SciPy are available.
