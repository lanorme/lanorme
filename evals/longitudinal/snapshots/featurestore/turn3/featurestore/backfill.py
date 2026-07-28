"""Chunked backfill: splitting a historical range into fixed-size windows
and tracking which windows are already done.

These are plain, storage-free helpers. The state they operate on --
which windows have been completed for a given feature version -- is kept
by :mod:`featurestore.registry`; the orchestration (compute a window,
upsert it, mark it done) is :meth:`featurestore.store.FeatureStore.backfill`.

Splitting the work into windows and recording each one as it commits is
what makes a backfill resumable: if a call to ``backfill`` is interrupted
(raises partway, or the caller simply stops running it), the windows
already committed stay recorded, and calling ``backfill`` again with the
same range skips them and continues from the first incomplete one --
without reprocessing, and so without double-writing, anything already
there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from featurestore.errors import InvalidRangeError

#: A half-open range: covers ``start`` up to but not including ``end``.
Range = tuple[Any, Any]


@dataclass(frozen=True)
class BackfillResult:
    """The outcome of one :meth:`featurestore.store.FeatureStore.backfill` call."""

    feature_name: str
    version: int
    windows_run: int
    windows_skipped: int
    rows_upserted: int
    completed_ranges: list[Range]


def chunk_windows(start: Any, end: Any, chunk_size: Any) -> list[Range]:
    """Split ``[start, end)`` into consecutive half-open windows of
    ``chunk_size`` each, the last one truncated to ``end``.

    Rejects a backwards or empty range, and a ``chunk_size`` that does not
    advance the range forward (for example zero or negative), both of
    which would otherwise loop forever.
    """
    if not start < end:
        raise InvalidRangeError(f"backfill range must have start < end, got {start!r} to {end!r}")
    windows: list[Range] = []
    cursor = start
    while cursor < end:
        window_end = cursor + chunk_size
        if not window_end > cursor:
            raise InvalidRangeError(f"chunk_size {chunk_size!r} does not advance the range forward")
        if window_end > end:
            window_end = end
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def is_covered(window: Range, completed: list[Range]) -> bool:
    """Whether ``window`` already falls entirely within one completed range."""
    start, end = window
    return any(c_start <= start and end <= c_end for c_start, c_end in completed)


def merge_ranges(ranges: list[Range]) -> list[Range]:
    """Coalesce overlapping or touching half-open ranges into the fewest,
    sorted by start.
    """
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda r: r[0])
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            if end > last_end:
                merged[-1] = (last_start, end)
        else:
            merged.append((start, end))
    return merged
