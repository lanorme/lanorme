# swe_04_log_analysis

Write `access_log_analysis.py`.

Input is a directory of nginx access logs in the combined format, some of them
gzipped, one file per day.

The module should:

- Stream-parse the logs without loading a whole file into memory.
- Bucket requests into one-minute windows and compute, per window: request
  count, error rate (5xx over total), p50/p95/p99 latency from the
  `$request_time` field appended at the end of each line, and bytes served.
- Group by normalised route: numeric and UUID path segments collapse to `:id`,
  so `/users/42/orders/7` becomes `/users/:id/orders/:id`.
- Flag anomalous windows: error rate above three standard deviations from the
  trailing hour's mean, or p99 latency more than double the trailing hour's
  p99.
- Emit the top offending routes for each flagged window.
- Print a summary and optionally write the per-window series as JSON.

Standard library only.
