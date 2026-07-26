# stream_dedup.py -- operator report

## What this is

`stream_dedup.py` is a standalone, standard-library-only Python module that
suppresses duplicate events (dicts with a string `event_id`) before they hit
the warehouse. It's built around one class, `StreamDeduplicator`:

```python
from stream_dedup import StreamDeduplicator

dedup = StreamDeduplicator(state_path="stream_dedup.state")
for event in incoming_events:
    if dedup.process(event):   # True = forward it, False = drop it (duplicate)
        warehouse.write(event)
```

It also ships a CLI (`python3 stream_dedup.py`) that reads newline-delimited
JSON events on stdin and writes the unique ones to stdout, for quick testing
or as a standalone filter stage. For real 50,000/sec production throughput,
embed the class directly in your ingestion pipeline rather than going through
stdin/stdout JSON -- see "Throughput" below.

## The core trade-off, read this first

The spec asks for two things that cannot both be had exactly at the same
time: **exact** deduplication, and a **hard 256 MB cap that holds forever**,
against a stream that can run at 50,000 events/sec indefinitely with
duplicates up to six hours apart.

Do the arithmetic on the worst case: 50,000/sec x 21,600 seconds (6 hours) is
1.08 billion events. If a meaningful fraction of those are genuinely
distinct, there is no way to keep an exact (or even near-exact,
low-collision) record of a billion-plus arbitrary strings in 256 MB -- that's
roughly a quarter of a byte per event, which isn't enough to encode even a
single bit of "have I seen this" per event at low error, let alone a real
identifier. This is arithmetic, not an implementation gap, and it holds
regardless of what data structure you reach for (Bloom filter, hash set,
database, anything).

So the design makes a deliberate, one-sided trade:

- **A real duplicate is never missed.** This is a hard guarantee, not a
  probability -- see "Why duplicates never leak through" below.
- **A brand-new event has some chance of being mistaken for a duplicate**
  (and dropped) if the true number of distinct ids arriving in a six-hour
  window is large relative to the 256 MB budget. How large that chance is
  depends entirely on your real traffic, and the tool tells you what it is,
  live, via metrics (see below) -- it does not hide the problem.

This is the safer direction to err in for a pipeline that sits in front of a
warehouse: over-suppressing occasionally trims a little data; under-suppressing
lets duplicates corrupt downstream aggregates. But "occasionally" needs to
actually be occasional in your environment, which is why the false-positive
rate is a first-class, monitored number here, not an afterthought.

**Action for you:** measure (or estimate) how many *distinct* `event_id`s
actually show up in a six-hour window in production. Pass it in as
`expected_unique_events_per_window` (or `--expected-unique-per-window` on the
CLI). If you don't, the tool assumes the worst case implied by the spec's own
numbers (50,000/sec all distinct), and the false-positive rate will be bad --
in testing with the defaults, the assumed worst case, this comes out to
roughly a **60-90% chance of wrongly dropping a new event**, which is
almost certainly not what you want. With a realistic distinct-id estimate
(e.g. a few million per window, which is far more typical for a system that,
per the spec, sees most duplicates arrive close together), the same 256 MB
budget gives a false-positive rate so low it rounds to zero. The gap between
those two numbers *is* your actual duplicate ratio, and it matters enormously
here -- this is the single most important tuning knob in this system.

## How it works

Two same-sized bit arrays ("generations"), each a rotating Bloom filter
generation:

- An incoming `event_id` is hashed once (BLAKE2b, 128-bit digest) and turned
  into `k` bit positions in the array via double hashing (`h1 + i*h2`), so
  only one hash computation is needed per event regardless of `k`.
- **Checking** an event queries every live generation; if any generation has
  all `k` bits set, it's flagged as a duplicate.
- **Recording** a new event sets its `k` bits in the *current* generation
  only.
- Each generation accumulates for `generation_seconds =
  retention_seconds / (num_generations - 1)`. When the current generation's
  span elapses, the oldest generation is wiped (all bits zeroed) and put back
  into service as the new current generation.
- With the default `num_generations=2` and `retention_seconds=21600` (6h),
  this guarantees a duplicate is caught for **at least 6 hours** and **at
  most 12 hours** after first being seen, matching "duplicates ... up to
  about six hours apart."

Memory is exactly the two bit arrays, sized once at construction from the
memory budget, never resized, never grown -- so "must not use more than
256 MB, ever, no matter how long it runs" holds by construction, not by
hoping GC keeps up.

### Why only two generations, not many

It's tempting to slice the 6-hour window into many small time buckets (say,
36 ten-minute buckets) for finer-grained expiry. Don't: querying a rotating
Bloom filter means checking *every* live generation, and each generation
independently contributes its own false-positive chance. Checking N
generations compounds those chances (roughly `1 - (1-p)^N`), so for a fixed
total memory budget, *more generations makes the overall false-positive rate
worse*, sometimes drastically -- in testing, 36 buckets over the same budget
and cardinality pushed the effective false-positive rate to nearly 100%,
versus ~89% (worst case) or ~0% (realistic case, see above) for two. Two
generations is the minimum that supports smooth rolling expiry at all (one
generation would mean abruptly forgetting everything on every rotation), and
it minimizes the compounding penalty. `num_generations` is configurable if
you have a specific reason to raise it, but the trade-off above applies.

### Why duplicates never leak through

A Bloom filter has no false negatives: if a bit was set by a real insert,
checking it later will always find it set. The only error direction is a
false positive (bits that happen to already be set for an id that was never
actually inserted). So as long as an id's insertion happened within the still
-live generations, a repeat of that id is caught with certainty. The only way
a genuine duplicate could leak through is if it arrives *after* the id has
aged out of both generations (i.e., more than the guaranteed retention window
apart) -- which is exactly the boundary the spec describes ("we have seen
them up to about six hours apart"), and which `retention_seconds` is sized to
cover.

## Restart safety

State (the two bit arrays plus small bookkeeping: generation start times,
per-generation insert counts, running totals) lives in **one file, memory
-mapped**. There is no separate "snapshot" step and no periodic multi-hundred
-megabyte serialization: every bit set during normal operation is a write
directly into that mapped file. A process restart -- clean or a hard `kill
-9` -- reopens the same file and resumes with everything the previous process
had already learned, with no gap and no explicit save/restore logic to get
wrong. This is what satisfies "restarting the process must not cause a flood
of duplicates to leak through": there is nothing to re-learn.

The only scenario this doesn't cover is a full machine crash or power loss
before the OS has written dirty pages back to disk. `flush()` (called
periodically during normal operation and always on graceful shutdown, e.g.
`SIGTERM`/`SIGINT` in the CLI) calls `msync`/`fsync` to bound that window. If
you run this in an environment where the underlying disk itself is unreliable
across power loss (rare for real disks with power-loss protection, more
plausible for some network/overlay filesystems), consider a shorter flush
interval, at the cost of more frequent syscalls.

Running with `state_path=None` skips persistence entirely (an in-memory
bytearray backs the bit arrays instead of a mapped file) -- useful for tests
or genuinely stateless/ephemeral use, but a restart in that mode is a cold
start.

If you change the memory budget, retention window, or generation count after
a state file already exists, the tool detects the mismatch on startup and
refuses to reuse the file (raises `ConfigMismatchError`) rather than silently
misinterpreting the bytes. Delete the old file (accepting a cold start) or
restore the original configuration.

## Metrics

`dedup.metrics()` returns a dict including:

- `hit_rate` -- duplicates / total processed, as requested.
- `memory_allocated_bytes` -- the fixed, hard-capped size of the mapped
  state (bit arrays + bookkeeping). This never changes after construction; it
  *is* the 256 MB guarantee, made inspectable.
- `process_memory_bytes` / `process_memory_metric` -- best-effort *actual*
  process resident memory, read from `/proc/self/status` (`VmRSS`, "current
  _rss") on Linux, falling back to `resource.getrusage().ru_maxrss` ("peak
  _rss", a high-water mark, not current) elsewhere. Use this to confirm the
  reserved overhead (24 MB by default, for Python/interpreter baseline and
  JSON scratch space -- tune `reserved_overhead_bytes` if your process runs
  heavier than that) is actually sufficient in your environment.
- `estimated_false_positive_rate` / `estimated_false_positive_rate_per_generation`
  -- computed from the *actual* number of inserts recorded per generation so
  far, using the standard Bloom filter formula. This is the number to alert
  on: if it's climbing higher than expected, your real distinct-id rate is
  higher than what you configured, and you should either raise
  `expected_unique_events_per_window` to match reality (which doesn't change
  memory used, only the informational `k`/expected numbers) or accept a
  higher genuine miss rate, or reduce `retention_seconds`.
- `expected_false_positive_rate` -- the *design-time* estimate from your
  configured `expected_unique_events_per_window` (or the rate-based default),
  for comparison against the live estimate above.
- `total_processed`, `total_duplicates`, `generation_live_counts`,
  `uptime_seconds`, `hash_functions_k`, and the configured
  `retention_seconds_guaranteed_minimum` / `num_generations` /
  `generation_seconds` for context.

The CLI emits this dict as one JSON line to stderr every
`--metrics-interval` seconds (default 10s).

## Throughput

On this development machine (single core, no I/O, pure `is_duplicate` calls
with no JSON overhead), a quick check inserting 200,000 distinct ids ran at
roughly 205,000 events/sec -- comfortably above the 50,000/sec target, with
headroom, in the worst case (every event is new, so every call does a full
insert; a call that finds an existing duplicate does less work, since it can
stop as soon as any generation matches). This is a single-threaded,
single-process number on one piece of hardware -- benchmark it in your actual
environment and under your actual `event_id` distribution before trusting it
in production.

The CLI itself (JSON parse per line, stdin/stdout) will run slower than the
bare class, because JSON decoding and line I/O add per-event Python overhead
on top of the dedup logic. If the CLI can't keep up with your real ingestion
rate, **embed `StreamDeduplicator` directly in your ingestion code** and call
`.process()` per event in whatever form it already arrives in (skipping JSON
serialization entirely). The CLI is provided as a reference/demo entry point
and for ad hoc filtering, not as the assumed production deployment shape.

## Known limitations

- **Not thread-safe.** No internal locking, by design, to keep the
  single-threaded hot path fast. If you need to call it from multiple
  threads, add your own lock around `process`/`is_duplicate`, or -- better
  for throughput -- run one `StreamDeduplicator` per worker, each with its
  own state file, partitioning the stream by `hash(event_id)` across workers.
- **Single writer per state file.** Two processes pointed at the same state
  file concurrently will race on rotation and corrupt each other's counters.
  Scale horizontally by sharding the stream across independent instances (and
  independent state files), not by sharing one file.
- **Developed and exercised on Linux.** The `mmap`-backed persistence and the
  `/proc/self/status` memory metric are Linux-specific paths; `mmap` itself
  is cross-platform via the standard library, but this hasn't been run on
  Windows or verified there.
- **`expected_unique_events_per_window` is only used for sizing `k` and for
  the informational expected-false-positive numbers** -- it never affects how
  much memory is allocated. The memory cap always applies, from the first
  event onward, no matter what you tell it to expect.
