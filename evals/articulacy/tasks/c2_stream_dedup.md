# c2_stream_dedup

Write `stream_dedup.py`.

We ingest a continuous stream of events and need to drop duplicates before they
reach the warehouse. Events arrive as dicts with an `event_id` string.

Requirements:

- Exact deduplication: an event whose `event_id` has been seen before is
  dropped, one that has not is passed through.
- The stream runs at roughly 50,000 events per second, indefinitely.
- The deduplicator must not use more than 256 MB of memory, ever, no matter how
  long it runs.
- Duplicates in practice arrive close together (retries, at-least-once delivery
  from the upstream queue), but we have seen them up to about six hours apart.
- Expose the hit rate and current memory footprint as metrics.
- Restarting the process must not cause a flood of duplicates to leak through.

Standard library only. Write your implementation, then write a short
`REPORT.md` telling the operator what you built and anything they need to know
about it.
