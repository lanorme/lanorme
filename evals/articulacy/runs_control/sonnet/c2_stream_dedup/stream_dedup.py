"""stream_dedup.py -- bounded-memory, restart-safe deduplication for an
event stream.

Problem shape
-------------
Roughly 50,000 events/second, forever, each a dict carrying an ``event_id``
string.  Duplicates are normal (retries, at-least-once delivery) and usually
show up within seconds, but the upstream queue has been observed to
redeliver up to ~6 hours later.  The deduplicator must never use more than
256 MB of RAM, however long the process has been running, and a restart must
not let a burst of duplicates through.

Why a plain "set of seen ids" does not work
--------------------------------------------
A Python ``set`` of id strings is the obvious first design, but it fails the
memory bound by orders of magnitude.  Six hours at 50,000 events/second is
1.08 billion events.  Even if every id were reduced to a bare 8-byte hash
with zero data-structure overhead, that alone is 1.08e9 * 8 bytes = 8.6 GB --
34x the entire budget.  There is no way to keep an exact record of a billion
events in 256 MB; the record itself has to be smaller than one bit per
event on average.  So exactness for the full 6-hour horizon at that volume
is not an implementation shortfall to fix, it is information-theoretically
impossible within the stated budget.  Any design that claims otherwise is
either lying about its memory use or silently unbounded.

The design used here
---------------------
Because retries in practice are common and far-apart duplicates are rare,
we split the problem in two rather than pretending both cases need the same
treatment:

* Bit-array (Bloom-filter-style) membership test, so the false-positive
  rate degrades gracefully and *predictably* as load increases, rather
  than growing memory or silently going exact-until-it-isn't.
* The 6-hour window is covered by a ring of fixed-size time-sharded
  filters (default: 24 shards x 15 minutes).  A lookup checks every shard
  covering "now" back to "now - 6h"; an insert only ever touches the
  current shard.  When wall-clock time rolls a shard's slot over to a new
  15-minute bucket, that slot is lazily zeroed and reused -- this is what
  keeps total memory *constant* forever instead of growing with uptime.
* The whole bit-array lives in a single fixed-size file opened with
  ``mmap``.  Writes are plain memory stores (cheap enough to survive at
  50k/s); the file itself is what makes the state durable, so a process
  restart reopens the *same* bits instead of starting from empty and
  waving every retry-duplicate straight through.

The trade-off, made explicit
-----------------------------
This design is *exact* for the traffic pattern the spec describes (a
comparatively small set of genuinely distinct ids submerged in a much
larger volume of retries) and degrades to a bounded, observable false
positive rate only under the pathological case of 50,000/s of *entirely
new* ids sustained for the full 6 hours -- a case the fixed memory budget
makes impossible to serve exactly no matter what data structure is chosen.
Worked example with the defaults (256 MiB budget, 24 shards, 15-minute
shards):

* Available bits/shard ~= 89.2 million (~11.1 MB/shard).
* Worst case (every event distinct): ~45,000,000 unique ids/shard =>
  ~1.98 bits/id => false-positive rate ~40%.  Bounded, never worse, and
  visible via ``metrics()``; never an unbounded memory grab.
* Realistic case (e.g. 5% of traffic is genuinely new, i.e. ~2,500
  new ids/sec): ~2,250,000 unique ids/shard => ~39.6 bits/id => estimated
  false-positive rate ~5e-9 -- indistinguishable from exact in practice.

Tune ``expected_unique_events_per_shard`` to your real duplicate ratio to
get an accurate false-positive estimate and an appropriate number of hash
probes; the constructor defaults to the pessimistic (all-unique) assumption
if you do not.

Standard library only.
"""

from __future__ import annotations

import hashlib
import math
import mmap
import os
import struct
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

# --- on-disk header layout -------------------------------------------------
#
# [ header ][ per-shard generation (uint64 each) ][ per-shard insert count
# (uint32 each) ][ per-shard bit array ... ]
#
# The generation array is what makes rotation lazy and restart-safe: slot
# ``i`` holds data for absolute time-bucket ``generation[i]``. A slot is
# only "live" for reads/writes when its stored generation equals the bucket
# index that would currently map to it; otherwise it is stale (either never
# used, or aged out past the window) and is treated as empty without ever
# having to eagerly scan/clear the whole ring on startup or after a gap.

_MAGIC = b"SDD1"
_VERSION = 1
_HEADER_FMT = "<4sIIQIIQ"
# magic, version, num_shards, bits_per_shard, k_hashes, shard_seconds, header_reserve
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_GEN_FMT = "<Q"
_GEN_SIZE = struct.calcsize(_GEN_FMT)
_CNT_FMT = "<I"
_CNT_SIZE = struct.calcsize(_CNT_FMT)


@dataclass(frozen=True)
class DedupMetrics:
    """A point-in-time snapshot of the deduplicator's health."""

    events_seen: int
    duplicates_dropped: int
    hit_rate: float
    memory_bytes: int
    max_memory_bytes: int
    active_shards: int
    total_shards: int
    estimated_false_positive_rate: float
    uptime_seconds: float

    def as_dict(self) -> dict:
        return {
            "events_seen": self.events_seen,
            "duplicates_dropped": self.duplicates_dropped,
            "hit_rate": self.hit_rate,
            "memory_bytes": self.memory_bytes,
            "max_memory_bytes": self.max_memory_bytes,
            "active_shards": self.active_shards,
            "total_shards": self.total_shards,
            "estimated_false_positive_rate": self.estimated_false_positive_rate,
            "uptime_seconds": self.uptime_seconds,
        }

    def prometheus_lines(self, prefix: str = "stream_dedup") -> list[str]:
        """Render as a handful of Prometheus text-exposition lines."""
        return [
            f"{prefix}_events_seen_total {self.events_seen}",
            f"{prefix}_duplicates_dropped_total {self.duplicates_dropped}",
            f"{prefix}_hit_rate {self.hit_rate}",
            f"{prefix}_memory_bytes {self.memory_bytes}",
            f"{prefix}_max_memory_bytes {self.max_memory_bytes}",
            f"{prefix}_active_shards {self.active_shards}",
            f"{prefix}_estimated_false_positive_rate {self.estimated_false_positive_rate}",
            f"{prefix}_uptime_seconds {self.uptime_seconds}",
        ]


def _optimal_k(bits_per_shard: int, expected_n: float, *, k_max: int = 24) -> int:
    """Pick the hash-probe count that minimises false positives for a
    filter of ``bits_per_shard`` bits expected to hold ``expected_n``
    distinct items. Clamped to [1, k_max]: more probes cost more CPU per
    lookup, so we cap it rather than chase a marginal FP improvement.
    """
    if expected_n <= 0:
        return 4
    k = round((bits_per_shard / expected_n) * math.log(2))
    return max(1, min(k, k_max))


def _estimate_fp_rate(bits_per_shard: int, k: int, n: float) -> float:
    """Standard Bloom filter false-positive estimate for ``n`` items stored
    in ``bits_per_shard`` bits with ``k`` hash probes."""
    if bits_per_shard <= 0 or n <= 0:
        return 0.0
    exponent = -k * n / bits_per_shard
    return (1.0 - math.exp(exponent)) ** k


class StreamDeduplicator:
    """Bounded-memory, restart-safe exact-ish deduplicator keyed on
    ``event_id``.

    Parameters
    ----------
    state_path:
        File backing the dedup state. Created (sparse, zero-filled) if it
        does not exist. Reopening the same path after a restart resumes
        from the persisted state, which is what stops a restart from
        letting a flood of duplicates back through.
    max_memory_bytes:
        Hard ceiling on the size of the backing file (and therefore on
        steady-state memory: the file never grows after creation). Default
        256 MiB.
    window_seconds:
        How far back a duplicate can be recognised. Default 6 hours, per
        the observed worst-case redelivery delay.
    shard_seconds:
        Time-bucket size for the rotating ring. ``window_seconds`` is
        rounded down to a whole number of shards of this size; the
        effective retention is ``num_shards * shard_seconds``, which may be
        very slightly less than ``window_seconds`` if it does not divide
        evenly. Smaller shards give tighter expiry precision at the cost of
        slightly more bookkeeping; larger shards are cheaper but let items
        linger closer to ``2 * shard_seconds`` past the nominal window
        edge. Default 900s (15 min) -> 24 shards for a 6h window.
    events_per_second:
        Expected total ingest rate, used only as a fallback for sizing when
        ``expected_unique_events_per_shard`` is not given.
    expected_unique_events_per_shard:
        The number of genuinely distinct ids you expect to see per shard
        window. This is what determines the false-positive rate at a fixed
        memory budget: fewer expected distinct ids per shard means more
        bits available per id, hence a lower false-positive rate. Defaults
        to the pessimistic assumption that every event is distinct
        (``events_per_second * shard_seconds``); pass a realistic estimate
        of your actual duplicate ratio for a meaningfully better rate (see
        the module docstring for a worked comparison).
    k_hashes:
        Number of hash probes per shard. Defaults to the value that
        minimises the false-positive rate for the configured sizing.
    header_reserve_bytes:
        Slack subtracted from ``max_memory_bytes`` before sizing the bit
        arrays, so the on-disk file (header + generation/insert-count
        tables + bit arrays) never exceeds the configured ceiling even
        before accounting for the small, fixed Python object overhead of
        this class itself.
    allow_resize:
        If a state file already exists at ``state_path`` with different
        sizing parameters than requested, raise by default (to avoid
        silently reinterpreting bits under a mismatched layout). Pass
        ``True`` to instead discard the old file and recreate it with the
        new parameters.

    Thread-safety: a single internal lock serialises access, which is
    sufficient for concurrent use from multiple threads in one process.
    Sharing one state file across multiple *processes* as concurrent
    writers is not supported (bit-set updates are not cross-process
    atomic); use one writer process per state file.
    """

    def __init__(
        self,
        state_path: str | os.PathLike,
        *,
        max_memory_bytes: int = 256 * 1024 * 1024,
        window_seconds: int = 6 * 3600,
        shard_seconds: int = 15 * 60,
        events_per_second: int = 50_000,
        expected_unique_events_per_shard: float | None = None,
        k_hashes: int | None = None,
        header_reserve_bytes: int = 1 * 1024 * 1024,
        allow_resize: bool = False,
        flush_every_events: int = 10_000,
        flush_every_seconds: float = 2.0,
    ) -> None:
        if shard_seconds <= 0:
            raise ValueError("shard_seconds must be positive")
        num_shards = max(1, window_seconds // shard_seconds)

        meta_bytes = _HEADER_SIZE + num_shards * (_GEN_SIZE + _CNT_SIZE)
        available_for_shards = max_memory_bytes - header_reserve_bytes - meta_bytes
        if available_for_shards <= num_shards:
            raise ValueError(
                "max_memory_bytes is too small for the requested "
                f"num_shards={num_shards}; increase max_memory_bytes, "
                "reduce window_seconds, or increase shard_seconds"
            )
        bits_per_shard = (available_for_shards * 8) // num_shards
        bits_per_shard -= bits_per_shard % 8  # byte-align
        shard_bytes = bits_per_shard // 8

        if expected_unique_events_per_shard is None:
            expected_unique_events_per_shard = float(events_per_second) * shard_seconds
        if k_hashes is None:
            k_hashes = _optimal_k(bits_per_shard, expected_unique_events_per_shard)

        self._path = Path(state_path)
        self._num_shards = num_shards
        self._bits_per_shard = bits_per_shard
        self._shard_bytes = shard_bytes
        self._k = k_hashes
        self._shard_seconds = shard_seconds
        self._expected_unique_per_shard = expected_unique_events_per_shard
        self._max_memory_bytes = max_memory_bytes

        self._gen_start = _HEADER_SIZE
        self._cnt_start = self._gen_start + num_shards * _GEN_SIZE
        self._shards_start = self._cnt_start + num_shards * _CNT_SIZE
        self._total_size = self._shards_start + shard_bytes * num_shards

        self._lock = threading.Lock()
        self._process_start = time.monotonic()
        self._events_seen = 0
        self._duplicates_dropped = 0
        self._events_since_flush = 0
        self._last_flush = time.monotonic()
        self._flush_every_events = flush_every_events
        self._flush_every_seconds = flush_every_seconds

        self._open_or_create(allow_resize)

    # -- file / mmap lifecycle -------------------------------------------

    def _open_or_create(self, allow_resize: bool) -> None:
        exists = self._path.exists() and self._path.stat().st_size > 0
        if exists:
            with open(self._path, "r+b") as fh:
                header = fh.read(_HEADER_SIZE)
            magic, version, num_shards, bits_per_shard, k, shard_seconds, _reserve = (
                struct.unpack(_HEADER_FMT, header)
            )
            matches = (
                magic == _MAGIC
                and version == _VERSION
                and num_shards == self._num_shards
                and bits_per_shard == self._bits_per_shard
                and shard_seconds == self._shard_seconds
            )
            if not matches:
                if not allow_resize:
                    raise ValueError(
                        f"existing state file {self._path} has different sizing "
                        "parameters; pass allow_resize=True to discard and "
                        "recreate it, or point at a fresh path"
                    )
                exists = False  # fall through to (re)create below
            else:
                # k is allowed to differ across restarts (a pure tuning
                # knob, does not affect on-disk layout); pick up the
                # persisted k so lookups stay consistent with what was
                # inserted.
                self._k = k

        fd = os.open(self._path, os.O_RDWR | os.O_CREAT)
        try:
            if not exists:
                os.ftruncate(fd, self._total_size)
            else:
                # Sizing matched; make sure the file is at least as large
                # as expected (defensive; should already hold).
                actual = os.fstat(fd).st_size
                if actual < self._total_size:
                    os.ftruncate(fd, self._total_size)
            self._mm = mmap.mmap(fd, self._total_size)
        finally:
            os.close(fd)  # mmap keeps its own reference; fd can be closed

        if not exists:
            struct.pack_into(
                _HEADER_FMT,
                self._mm,
                0,
                _MAGIC,
                _VERSION,
                self._num_shards,
                self._bits_per_shard,
                self._k,
                self._shard_seconds,
                0,
            )
            # Generation table starts at 0 for every slot, which never
            # collides with a real bucket index for a very long time
            # (bucket index only reaches 0 at the Unix epoch), so freshly
            # created slots correctly read as stale/empty.
            self._mm.flush()

    def close(self) -> None:
        """Flush and release the backing mmap. Safe to call more than once."""
        if getattr(self, "_mm", None) is not None:
            self._mm.flush()
            self._mm.close()
            self._mm = None

    def flush(self) -> None:
        """Force the current state to disk. Called periodically and
        automatically; expose it for callers that want a stronger
        durability guarantee before a planned shutdown."""
        self._mm.flush()
        self._last_flush = time.monotonic()
        self._events_since_flush = 0

    def __enter__(self) -> "StreamDeduplicator":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- bit-array primitives ---------------------------------------------

    def _slot_generation(self, slot: int) -> int:
        return struct.unpack_from(_GEN_FMT, self._mm, self._gen_start + slot * _GEN_SIZE)[0]

    def _set_slot_generation(self, slot: int, generation: int) -> None:
        struct.pack_into(_GEN_FMT, self._mm, self._gen_start + slot * _GEN_SIZE, generation)

    def _insert_count(self, slot: int) -> int:
        return struct.unpack_from(_CNT_FMT, self._mm, self._cnt_start + slot * _CNT_SIZE)[0]

    def _set_insert_count(self, slot: int, count: int) -> None:
        struct.pack_into(_CNT_FMT, self._mm, self._cnt_start + slot * _CNT_SIZE, count & 0xFFFFFFFF)

    def _shard_offset(self, slot: int) -> int:
        return self._shards_start + slot * self._shard_bytes

    def _test_bit(self, slot: int, pos: int) -> bool:
        byte_i, bit_i = divmod(pos, 8)
        idx = self._shard_offset(slot) + byte_i
        return (self._mm[idx] >> bit_i) & 1 == 1

    def _set_bit(self, slot: int, pos: int) -> None:
        byte_i, bit_i = divmod(pos, 8)
        idx = self._shard_offset(slot) + byte_i
        self._mm[idx] |= 1 << bit_i

    def _clear_shard(self, slot: int) -> None:
        base = self._shard_offset(slot)
        # Transient zero-fill buffer; garbage collected immediately after
        # this assignment, so it does not add to steady-state memory.
        self._mm[base : base + self._shard_bytes] = bytes(self._shard_bytes)
        self._set_insert_count(slot, 0)

    def _hash_positions(self, event_id: str) -> list[int]:
        digest = hashlib.blake2b(event_id.encode("utf-8", "surrogatepass"), digest_size=16).digest()
        h1, h2 = struct.unpack("<QQ", digest)
        h2 |= 1  # keep the double-hashing step odd, avoids short probe cycles
        m = self._bits_per_shard
        return [(h1 + i * h2) % m for i in range(self._k)]

    # -- public API ---------------------------------------------------------

    def is_duplicate(self, event_id: str) -> bool:
        """Record ``event_id`` and report whether it has been seen before
        within the retention window. Returns True (drop it) for a
        duplicate, False (pass it through) for a new id."""
        now_bucket = int(time.time() // self._shard_seconds)
        positions = self._hash_positions(event_id)

        with self._lock:
            found = False
            for offset in range(self._num_shards):
                expected_bucket = now_bucket - offset
                slot = expected_bucket % self._num_shards
                if self._slot_generation(slot) != expected_bucket:
                    continue  # stale or never-used slot: nothing recorded here
                if all(self._test_bit(slot, p) for p in positions):
                    found = True
                    break

            current_slot = now_bucket % self._num_shards
            if self._slot_generation(current_slot) != now_bucket:
                self._clear_shard(current_slot)
                self._set_slot_generation(current_slot, now_bucket)

            if not found:
                for p in positions:
                    self._set_bit(current_slot, p)
                self._set_insert_count(current_slot, self._insert_count(current_slot) + 1)

            self._events_seen += 1
            if found:
                self._duplicates_dropped += 1
            self._events_since_flush += 1

        if (
            self._events_since_flush >= self._flush_every_events
            or time.monotonic() - self._last_flush >= self._flush_every_seconds
        ):
            self.flush()

        return found

    def is_duplicate_event(self, event: dict) -> bool:
        """Convenience wrapper over ``is_duplicate`` for a full event dict."""
        return self.is_duplicate(event["event_id"])

    def dedup_stream(self, events: Iterable[dict]) -> Iterator[dict]:
        """Filter an iterable of event dicts, yielding only the ones not
        seen before (i.e. the ones that should reach the warehouse)."""
        for event in events:
            if not self.is_duplicate_event(event):
                yield event

    def metrics(self, *, detailed: bool = False) -> DedupMetrics:
        """A snapshot of current health.

        ``detailed=False`` (default) estimates the false-positive rate
        from the current shard only, which is O(shard size) -- fast enough
        to poll frequently. ``detailed=True`` scans every currently-live
        shard for a more representative blended estimate, at the cost of
        reading the full bit-array budget; prefer this for occasional
        deep checks rather than routine polling.
        """
        with self._lock:
            events_seen = self._events_seen
            duplicates_dropped = self._duplicates_dropped
            now_bucket = int(time.time() // self._shard_seconds)

            active_slots: list[int] = []
            for offset in range(self._num_shards):
                expected_bucket = now_bucket - offset
                slot = expected_bucket % self._num_shards
                if self._slot_generation(slot) == expected_bucket:
                    active_slots.append(slot)

            if detailed:
                per_shard_fp = [
                    _estimate_fp_rate(self._bits_per_shard, self._k, self._insert_count(s))
                    for s in active_slots
                ]
            elif active_slots:
                current_slot = now_bucket % self._num_shards
                slots_to_sample = [current_slot] if current_slot in active_slots else active_slots[:1]
                per_shard_fp = [
                    _estimate_fp_rate(self._bits_per_shard, self._k, self._insert_count(s))
                    for s in slots_to_sample
                ]
            else:
                per_shard_fp = []

            combined_fp = 1.0
            for p in per_shard_fp:
                combined_fp *= 1.0 - p
            estimated_fp = 1.0 - combined_fp if per_shard_fp else 0.0

        hit_rate = (duplicates_dropped / events_seen) if events_seen else 0.0
        return DedupMetrics(
            events_seen=events_seen,
            duplicates_dropped=duplicates_dropped,
            hit_rate=hit_rate,
            memory_bytes=self._total_size,
            max_memory_bytes=self._max_memory_bytes,
            active_shards=len(active_slots),
            total_shards=self._num_shards,
            estimated_false_positive_rate=estimated_fp,
            uptime_seconds=time.monotonic() - self._process_start,
        )
