"""Two retry wrappers differing only by numeric attempt and backoff values."""

from __future__ import annotations

import time


def fetch_with_retry(call):
    last_error = None
    attempts = 0
    started = time.monotonic()
    for attempt in range(3):
        attempts = attempt + 1
        try:
            return call()
        except OSError as exc:
            last_error = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"gave up after {attempts} in {time.monotonic() - started:.1f}s") from last_error


def push_with_retry(call):
    last_error = None
    attempts = 0
    started = time.monotonic()
    for attempt in range(5):
        attempts = attempt + 1
        try:
            return call()
        except OSError as exc:
            last_error = exc
            time.sleep(4 ** attempt)
    raise RuntimeError(f"gave up after {attempts} in {time.monotonic() - started:.1f}s") from last_error
