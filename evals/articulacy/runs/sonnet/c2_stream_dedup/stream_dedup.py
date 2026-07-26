#!/usr/bin/env python3
"""stream_dedup.py -- bounded-memory duplicate suppression for a high-rate event stream.

Problem shape (see the task spec this was written against):

  * Events are dicts with a string ``event_id``. An id seen before must be
    dropped; a new one must pass through.
  * The stream runs at roughly 50,000 events/sec, indefinitely.
  * The deduplicator must never use more than 256 MB of memory, no matter how
    long it has been running.
  * Duplicates are usually close together in time (retry storms), but have
    been observed up to about six hours apart.
  * Hit rate and current memory footprint must be exposed as metrics.
  * A process restart must not let a flood of duplicates back through.

Why this can't be literally "exact": tracking every distinct event_id ever
seen, for six hours, at 50,000 events/sec, in the worst case (traffic that
happens to be almost all-unique) means remembering on the order of a billion
ids. There is no way to store a billion arbitrary strings -- or even a billion
compact fingerprints with a low collision rate -- in 256 MB. That is not an
implementation shortcoming, it is arithmetic (see REPORT.md for the numbers).

So this module trades exactness for a hard, unconditional memory ceiling, and
it picks the trade-off to fail in the safe direction:

  * A true duplicate is *never* missed (zero false negatives, always).
  * A brand-new event has a small, *measured and reported* chance of being
    mistaken for a duplicate and dropped (a false positive). How small depends
    on how many genuinely distinct ids actually show up in a six-hour window,
    which the operator should measure and feed in via
    ``expected_unique_events_per_window`` -- see REPORT.md.

The core data structure is a two-generation rotating Bloom filter: two
same-sized bit arrays ("generations"), each accumulating inserts for a
configurable span; when the older one falls out of the retention window it is
wiped and put back into service as the new generation. Memory is therefore
exactly two bit arrays, allocated once, forever -- it cannot grow. Using only
two generations (rather than many small ones) matters: querying a rotating
Bloom filter means checking every live generation, and each check contributes
its own false-positive chance, so more generations compound into a *worse*
overall false-positive rate for the same total memory. Two is the minimum
that supports smooth rolling expiry, so it is also close to the best
achievable here.

State (both generations' bits plus small bookkeeping counters) lives in a
single file, memory-mapped. That gets persistence across restarts for free:
the bits are the file, so a process restart -- even an unclean one -- resumes
with everything the previous process had learned, without an explicit
snapshot/restore step.

Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import mmap
import os
import signal
import struct
import sys
import time
from types import TracebackType
from typing import Callable, Optional

# --------------------------------------------------------------------------
# On-disk / in-memory layout
# --------------------------------------------------------------------------
#
#   [ fixed header ]
#   [ num_generations x float64  ]   generation start times (epoch seconds)
#   [ num_generations x uint64   ]   live insert count per generation
#   [ num_generations x gen_bytes ]  the bit arrays themselves, back to back
#
# The header format is versioned so a state file built with different
# parameters is detected and rejected rather than silently misread.

_MAGIC = b"SDD1"
_FORMAT_VERSION = 1

# magic, version, num_generations, pad, gen_bytes, k, retention_seconds,
# generation_seconds, current_gen, created_at, total_seen, total_duplicates
_HEADER_STRUCT = struct.Struct("<4sBBHQIddIdQQ")


class ConfigMismatchError(RuntimeError):
    """Raised when an existing state file doesn't match the requested config."""


def _blake2b_positions(event_id: str, gen_bits: int, k: int) -> list[int]:
    """Map an event_id to k bit positions in a gen_bits-wide array.

    Uses one 128-bit BLAKE2b digest, split into two 64-bit words, combined via
    the standard Kirsch-Mitzenmacher double-hashing trick (pos_i = h1 + i*h2).
    This gives k well-distributed positions from a single hash computation,
    which matters at 50,000 events/sec: hashing is the dominant per-event
    cost, so computing it once instead of k times is a real saving.
    """
    digest = hashlib.blake2b(
        event_id.encode("utf-8", "surrogatepass"), digest_size=16
    ).digest()
    h1, h2 = struct.unpack("<QQ", digest)
    h2 |= 1  # must be odd relative to a power-of-two modulus for good spread
    return [(h1 + i * h2) % gen_bits for i in range(k)]


def _current_process_memory_bytes() -> tuple[Optional[int], str]:
    """Best-effort *current* (not peak) resident memory of this process.

    Prefers /proc/self/status (Linux, gives true current RSS). Falls back to
    resource.getrusage's ru_maxrss, which is a *peak* figure, not current --
    callers get told which kind they received via the second return value.
    Returns (None, "unavailable") if neither is obtainable (e.g. Windows).
    """
    try:
        with open("/proc/self/status", "r", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return kb * 1024, "current_rss"
    except OSError:
        pass
    try:
        import resource

        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KB, macOS/BSD reports bytes.
        if sys.platform == "darwin":
            return maxrss, "peak_rss"
        return maxrss * 1024, "peak_rss"
    except Exception:
        return None, "unavailable"


def _expected_false_positive_rate(gen_bits: int, k: int, n: float) -> float:
    """Standard Bloom filter false-positive estimate for n inserted elements."""
    if n <= 0:
        return 0.0
    return (1.0 - math.exp(-k * n / gen_bits)) ** k


class StreamDeduplicator:
    """Bounded-memory, restart-safe, near-exact duplicate suppression.

    Call ``process(event)`` per incoming event; it returns True if the event
    should be forwarded (first time seen) and False if it should be dropped
    (a duplicate, or -- rarely -- a false positive; see the module docstring
    and REPORT.md for what that means in practice).
    """

    def __init__(
        self,
        *,
        retention_seconds: float = 6 * 3600,
        num_generations: int = 2,
        memory_budget_bytes: int = 256 * 1024 * 1024,
        reserved_overhead_bytes: int = 24 * 1024 * 1024,
        expected_rate_events_per_second: float = 50_000.0,
        expected_unique_events_per_window: Optional[float] = None,
        state_path: Optional[str] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if num_generations < 2:
            raise ValueError(
                "num_generations must be >= 2 (need at least a current and a "
                "previous generation for rolling expiry)"
            )
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")

        self._clock = clock
        self._num_generations = num_generations
        self._retention_seconds = float(retention_seconds)
        # Guaranteed minimum retention = (num_generations - 1) * generation_seconds.
        # Solving for generation_seconds so that minimum equals the requested
        # retention exactly (extra retention beyond that is a bonus, not a
        # promise: at most num_generations * generation_seconds).
        self._generation_seconds = self._retention_seconds / (num_generations - 1)

        header_size = _HEADER_STRUCT.size
        bookkeeping = num_generations * (8 + 8)  # start times + counts
        available = memory_budget_bytes - reserved_overhead_bytes - header_size - bookkeeping
        if available < num_generations * 64:
            raise ValueError(
                "memory_budget_bytes is too small once header, per-generation "
                "bookkeeping, and reserved overhead are subtracted"
            )
        gen_bytes = available // num_generations
        self._gen_bytes = gen_bytes
        self._gen_bits = gen_bytes * 8

        expected_n_per_gen = (
            expected_unique_events_per_window
            if expected_unique_events_per_window is not None
            else expected_rate_events_per_second * self._generation_seconds
        )
        self._expected_n_per_gen = expected_n_per_gen
        k = max(1, round((self._gen_bits / max(expected_n_per_gen, 1)) * math.log(2)))
        self._k = min(k, 20)

        self.expected_false_positive_rate_per_generation = _expected_false_positive_rate(
            self._gen_bits, self._k, expected_n_per_gen
        )
        self.expected_false_positive_rate = 1.0 - (
            1.0 - self.expected_false_positive_rate_per_generation
        ) ** num_generations

        self._header_size = header_size
        self._starts_offset = header_size
        self._counts_offset = self._starts_offset + num_generations * 8
        self._bits_offset = self._counts_offset + num_generations * 8
        self._total_size = self._bits_offset + num_generations * gen_bytes

        self._start_time = self._clock()
        self._state_path = state_path
        self._file = None
        self._buf: bytearray | mmap.mmap
        self._current_gen = 0
        self._gen_start = [self._start_time] * num_generations
        self._gen_counts = [0] * num_generations
        self._total_seen = 0
        self._total_duplicates = 0
        self._created_at = self._start_time
        self._events_since_sync = 0
        self._last_sync_time = self._start_time

        if state_path is None:
            self._buf = bytearray(self._total_size)
            self._persistent = False
        else:
            self._buf = self._open_or_create_mmap(state_path)
            self._persistent = True
            self._load_header()

    # -- file / mmap setup -------------------------------------------------

    def _open_or_create_mmap(self, path: str) -> mmap.mmap:
        exists = os.path.exists(path) and os.path.getsize(path) > 0
        self._file = open(path, "r+b" if exists else "w+b")
        if not exists:
            self._file.truncate(self._total_size)
            self._file.flush()
        else:
            actual_size = os.path.getsize(path)
            if actual_size != self._total_size:
                self._file.close()
                raise ConfigMismatchError(
                    f"state file {path!r} is {actual_size} bytes, but the "
                    f"requested configuration needs {self._total_size} bytes. "
                    "The configuration (memory budget, retention, or "
                    "generation count) has changed since this file was "
                    "written. Delete the file to start fresh, or restore the "
                    "original configuration."
                )
        buf = mmap.mmap(self._file.fileno(), self._total_size)
        self._buf = buf
        if not exists:
            self._write_header(fresh=True)
        return buf

    def _write_header(self, *, fresh: bool) -> None:
        if fresh:
            for g in range(self._num_generations):
                self._gen_start[g] = self._start_time
        header = _HEADER_STRUCT.pack(
            _MAGIC,
            _FORMAT_VERSION,
            self._num_generations,
            0,
            self._gen_bytes,
            self._k,
            self._retention_seconds,
            self._generation_seconds,
            self._current_gen,
            self._created_at,
            self._total_seen,
            self._total_duplicates,
        )
        self._buf[0 : len(header)] = header
        starts = struct.pack(f"<{self._num_generations}d", *self._gen_start)
        self._buf[self._starts_offset : self._starts_offset + len(starts)] = starts
        counts = struct.pack(f"<{self._num_generations}Q", *self._gen_counts)
        self._buf[self._counts_offset : self._counts_offset + len(counts)] = counts

    def _load_header(self) -> None:
        raw = bytes(self._buf[: _HEADER_STRUCT.size])
        (
            magic,
            version,
            num_generations,
            _pad,
            gen_bytes,
            k,
            retention_seconds,
            generation_seconds,
            current_gen,
            created_at,
            total_seen,
            total_duplicates,
        ) = _HEADER_STRUCT.unpack(raw)
        if magic != _MAGIC or version != _FORMAT_VERSION:
            raise ConfigMismatchError(
                "state file header is not a recognised stream_dedup state file"
            )
        if num_generations != self._num_generations or gen_bytes != self._gen_bytes:
            raise ConfigMismatchError(
                "state file was written with a different num_generations or "
                "memory budget than requested now; delete it or match the "
                "original configuration"
            )
        # k, retention, and generation span are recomputed from current
        # parameters, but if they drifted while gen_bytes/num_generations did
        # not, the operator changed expected-cardinality assumptions rather
        # than the physical layout -- that's fine, only the bits matter for
        # correctness, so we keep the persisted k rather than silently
        # re-deriving it, to stay consistent with what was actually inserted.
        self._k = k
        self._retention_seconds = retention_seconds
        self._generation_seconds = generation_seconds
        self._current_gen = current_gen
        self._created_at = created_at
        self._total_seen = total_seen
        self._total_duplicates = total_duplicates
        starts_raw = bytes(
            self._buf[self._starts_offset : self._starts_offset + self._num_generations * 8]
        )
        self._gen_start = list(struct.unpack(f"<{self._num_generations}d", starts_raw))
        counts_raw = bytes(
            self._buf[self._counts_offset : self._counts_offset + self._num_generations * 8]
        )
        self._gen_counts = list(struct.unpack(f"<{self._num_generations}Q", counts_raw))

    def _sync_header(self) -> None:
        header = _HEADER_STRUCT.pack(
            _MAGIC,
            _FORMAT_VERSION,
            self._num_generations,
            0,
            self._gen_bytes,
            self._k,
            self._retention_seconds,
            self._generation_seconds,
            self._current_gen,
            self._created_at,
            self._total_seen,
            self._total_duplicates,
        )
        self._buf[0 : len(header)] = header
        starts = struct.pack(f"<{self._num_generations}d", *self._gen_start)
        self._buf[self._starts_offset : self._starts_offset + len(starts)] = starts
        counts = struct.pack(f"<{self._num_generations}Q", *self._gen_counts)
        self._buf[self._counts_offset : self._counts_offset + len(counts)] = counts
        self._events_since_sync = 0
        self._last_sync_time = self._clock()

    # -- bit array primitives ----------------------------------------------

    def _gen_base(self, gen: int) -> int:
        return self._bits_offset + gen * self._gen_bytes

    def _get_bit(self, base: int, pos: int) -> bool:
        idx = base + (pos >> 3)
        mask = 1 << (pos & 7)
        return (self._buf[idx] & mask) != 0

    def _set_bit(self, base: int, pos: int) -> None:
        idx = base + (pos >> 3)
        mask = 1 << (pos & 7)
        self._buf[idx] |= mask

    def _clear_generation(self, gen: int) -> None:
        base = self._gen_base(gen)
        self._buf[base : base + self._gen_bytes] = bytes(self._gen_bytes)

    # -- rotation ------------------------------------------------------------

    def _maybe_rotate(self, now: float) -> None:
        span = self._generation_seconds
        elapsed = now - self._gen_start[self._current_gen]
        if elapsed < span:
            return
        steps = int(elapsed // span)
        if steps >= self._num_generations:
            # Idle longer than the whole retention window: nothing in any
            # generation is still valid. Reset everything rather than
            # looping num_generations times for no benefit.
            for g in range(self._num_generations):
                self._clear_generation(g)
                self._gen_counts[g] = 0
                self._gen_start[g] = now - g * span
            self._current_gen = 0
            self._sync_header()
            return
        start = self._gen_start[self._current_gen]
        for _ in range(steps):
            self._current_gen = (self._current_gen + 1) % self._num_generations
            self._clear_generation(self._current_gen)
            self._gen_counts[self._current_gen] = 0
            start += span
            self._gen_start[self._current_gen] = start
        self._sync_header()

    # -- public API ----------------------------------------------------------

    def is_duplicate(self, event_id: str, *, now: Optional[float] = None) -> bool:
        """Return True if event_id has been seen within the retention window.

        Never false-negative: a real duplicate is always caught. Rarely
        false-positive: a brand-new id can be mistaken for a duplicate; see
        ``metrics()`` for the live estimate of how often that happens.
        """
        now = self._clock() if now is None else now
        self._maybe_rotate(now)
        positions = _blake2b_positions(event_id, self._gen_bits, self._k)

        duplicate = False
        for gen in range(self._num_generations):
            base = self._gen_base(gen)
            if all(self._get_bit(base, pos) for pos in positions):
                duplicate = True
                break

        self._total_seen += 1
        if duplicate:
            self._total_duplicates += 1
        else:
            base = self._gen_base(self._current_gen)
            for pos in positions:
                self._set_bit(base, pos)
            self._gen_counts[self._current_gen] += 1

        self._events_since_sync += 1
        if self._persistent and (
            self._events_since_sync >= 1000 or (now - self._last_sync_time) >= 5.0
        ):
            self._sync_header()
        return duplicate

    def process(self, event: dict, *, now: Optional[float] = None) -> bool:
        """Return True if event should be forwarded, False if it should be dropped."""
        try:
            event_id = event["event_id"]
        except (TypeError, KeyError) as exc:
            raise ValueError("event must be a dict with an 'event_id' key") from exc
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id must be a non-empty string")
        return not self.is_duplicate(event_id, now=now)

    def metrics(self) -> dict:
        total = self._total_seen
        hit_rate = (self._total_duplicates / total) if total else 0.0
        process_memory_bytes, memory_metric_kind = _current_process_memory_bytes()
        estimated_fp_per_gen = [
            _expected_false_positive_rate(self._gen_bits, self._k, count)
            for count in self._gen_counts
        ]
        estimated_union_fp = 1.0
        for p in estimated_fp_per_gen:
            estimated_union_fp *= 1.0 - p
        estimated_union_fp = 1.0 - estimated_union_fp
        return {
            "total_processed": total,
            "total_duplicates": self._total_duplicates,
            "hit_rate": hit_rate,
            "memory_allocated_bytes": self._total_size,
            "process_memory_bytes": process_memory_bytes,
            "process_memory_metric": memory_metric_kind,
            "num_generations": self._num_generations,
            "generation_seconds": self._generation_seconds,
            "retention_seconds_guaranteed_minimum": self._retention_seconds,
            "hash_functions_k": self._k,
            "generation_live_counts": list(self._gen_counts),
            "estimated_false_positive_rate_per_generation": estimated_fp_per_gen,
            "estimated_false_positive_rate": estimated_union_fp,
            "expected_false_positive_rate": self.expected_false_positive_rate,
            "uptime_seconds": self._clock() - self._start_time,
            "state_path": self._state_path,
        }

    def flush(self) -> None:
        if self._persistent:
            self._sync_header()
            assert isinstance(self._buf, mmap.mmap)
            self._buf.flush()
            if self._file is not None:
                self._file.flush()
                os.fsync(self._file.fileno())

    def close(self) -> None:
        self.flush()
        if isinstance(self._buf, mmap.mmap):
            self._buf.close()
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> "StreamDeduplicator":
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()


# --------------------------------------------------------------------------
# CLI: a reference / demo entry point that pipes newline-delimited JSON
# events on stdin to stdout, dropping duplicates. For production throughput
# at tens of thousands of events/sec, embed StreamDeduplicator directly in
# the ingestion pipeline instead -- see REPORT.md.
# --------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Deduplicate a stream of newline-delimited JSON events (each a "
            "dict with an 'event_id' string) read from stdin, writing "
            "unique events to stdout."
        )
    )
    parser.add_argument(
        "--state-file",
        default="stream_dedup.state",
        help="Path to the persistent state file (default: %(default)s). "
        "Pass an empty string to run in-memory only (no restart safety).",
    )
    parser.add_argument(
        "--memory-budget-mb",
        type=int,
        default=256,
        help="Hard memory cap in MiB (default: %(default)s).",
    )
    parser.add_argument(
        "--retention-hours",
        type=float,
        default=6.0,
        help="Guaranteed minimum duplicate-detection window, in hours "
        "(default: %(default)s).",
    )
    parser.add_argument(
        "--expected-rate",
        type=float,
        default=50_000.0,
        help="Assumed steady-state events/sec, used only to size the hash "
        "count (does not affect the memory cap). Prefer "
        "--expected-unique-per-window if you know real cardinality.",
    )
    parser.add_argument(
        "--expected-unique-per-window",
        type=float,
        default=None,
        help="Measured/estimated distinct event_ids per retention window. "
        "Overrides --expected-rate for sizing accuracy.",
    )
    parser.add_argument(
        "--generations",
        type=int,
        default=2,
        help="Number of rotating generations (default: %(default)s, the "
        "minimum; raising this trades a worse false-positive rate for "
        "finer-grained expiry, see REPORT.md).",
    )
    parser.add_argument(
        "--metrics-interval",
        type=float,
        default=10.0,
        help="Seconds between metrics lines on stderr (default: %(default)s).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    state_path = args.state_file or None

    dedup = StreamDeduplicator(
        retention_seconds=args.retention_hours * 3600,
        num_generations=args.generations,
        memory_budget_bytes=args.memory_budget_mb * 1024 * 1024,
        expected_rate_events_per_second=args.expected_rate,
        expected_unique_events_per_window=args.expected_unique_per_window,
        state_path=state_path,
    )

    stop = {"flag": False}

    def _handle_signal(signum: int, frame: object) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    last_metrics_emit = time.monotonic()
    try:
        for line in sys.stdin:
            if stop["flag"]:
                break
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(f"stream_dedup: skipping malformed line: {line[:200]!r}", file=sys.stderr)
                continue
            try:
                pass_through = dedup.process(event)
            except ValueError as exc:
                print(f"stream_dedup: skipping invalid event: {exc}", file=sys.stderr)
                continue
            if pass_through:
                sys.stdout.write(line + "\n")

            now_mono = time.monotonic()
            if now_mono - last_metrics_emit >= args.metrics_interval:
                sys.stdout.flush()
                print(json.dumps(dedup.metrics()), file=sys.stderr)
                sys.stderr.flush()
                last_metrics_emit = now_mono
    finally:
        sys.stdout.flush()
        dedup.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
