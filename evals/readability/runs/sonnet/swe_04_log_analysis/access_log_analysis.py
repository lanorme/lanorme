#!/usr/bin/env python3
"""Analyse a directory of nginx access logs for latency and error anomalies.

The tool expects one log file per day, in nginx's "combined" format with an
extra ``$request_time`` field appended to the end of each line, e.g.::

    203.0.113.7 - - [10/Oct/2023:13:55:36 +0000] "GET /users/42 HTTP/1.1" \
200 612 "-" "curl/8.4.0" 0.083

Files may be plain text or gzip-compressed (detected by content, not by file
extension), and are read one line at a time so that a directory of large logs
never needs to be loaded into memory in full.

Requests are bucketed into fixed one-minute windows. For each window we track,
per normalised route (numeric and UUID path segments collapsed to ``:id``),
the request count, error count, byte total and latencies, then roll those up
into window-level statistics: request count, error rate, p50/p95/p99 latency
and bytes served. A window is flagged as anomalous when its error rate is more
than three standard deviations above the mean of the trailing hour's per-minute
error rates, or when its p99 latency is more than double the p99 latency
computed over the trailing hour's pooled requests. Flagged windows report
their most offending routes (ranked by error count, then p99 latency).

Standard library only.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import re
import statistics
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

# --------------------------------------------------------------------------
# Configuration constants
# --------------------------------------------------------------------------

WINDOW_SECONDS = 60
TRAILING_WINDOWS = 60  # one hour of one-minute windows
MIN_HISTORY_WINDOWS = 10  # don't flag anomalies until we have this much history
GRACE_WINDOWS = 2  # tolerate this many minutes of out-of-order arrival
ERROR_STD_THRESHOLD = 3.0
LATENCY_MULTIPLIER = 2.0
DEFAULT_TOP_N = 5
RESERVOIR_CAPACITY = 100_000  # bounds memory for the global percentile estimate

_LOG_LINE_RE = re.compile(
    r"""
    ^(?P<remote_addr>\S+)\s+\S+\s+\S+\s+
    \[(?P<time_local>[^\]]+)\]\s+
    "(?P<request>[^"]*)"\s+
    (?P<status>\d{3})\s+
    (?P<bytes_sent>\S+)\s+
    "(?P<referer>[^"]*)"\s+
    "(?P<user_agent>[^"]*)"\s+
    (?P<request_time>\S+)\s*$
    """,
    re.VERBOSE,
)

_TIME_LOCAL_FORMAT = "%d/%b/%Y:%H:%M:%S %z"

_NUMERIC_SEGMENT_RE = re.compile(r"^\d+$")
_UUID_SEGMENT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_GZIP_MAGIC = b"\x1f\x8b"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LogRecord:
    """A single parsed access log entry."""

    timestamp: datetime
    method: str
    route: str
    status: int
    bytes_sent: int
    request_time: float | None  # seconds; None if not recorded ("-")

    @property
    def is_error(self) -> bool:
        return self.status >= 500


def normalise_route(path: str) -> str:
    """Collapse numeric and UUID path segments to ``:id``.

    ``/users/42/orders/7`` -> ``/users/:id/orders/:id``. Query strings and
    fragments are dropped; the empty path collapses to ``/``.
    """
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path:
        return "/"
    segments = path.split("/")
    normalised = [
        ":id"
        if seg and (_NUMERIC_SEGMENT_RE.match(seg) or _UUID_SEGMENT_RE.match(seg))
        else seg
        for seg in segments
    ]
    result = "/".join(normalised)
    return result or "/"


def _parse_request_field(request: str) -> tuple[str, str]:
    """Split the quoted ``"$request"`` field into (method, path).

    Malformed or empty request lines (common for rejected/aborted
    connections) fall back to a placeholder method and the raw text as path.
    """
    parts = request.split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "-", request or "-"


def parse_line(line: str) -> LogRecord | None:
    """Parse one combined-format log line with a trailing request_time.

    Returns ``None`` for blank or unparseable lines rather than raising, so a
    single corrupt line never aborts the analysis of a whole file.
    """
    line = line.rstrip("\n")
    if not line.strip():
        return None
    match = _LOG_LINE_RE.match(line)
    if match is None:
        return None

    try:
        timestamp = datetime.strptime(match["time_local"], _TIME_LOCAL_FORMAT)
    except ValueError:
        return None

    method, path = _parse_request_field(match["request"])
    route = normalise_route(path)

    bytes_field = match["bytes_sent"]
    bytes_sent = int(bytes_field) if bytes_field.isdigit() else 0

    request_time_field = match["request_time"]
    try:
        request_time: float | None = float(request_time_field)
    except ValueError:
        request_time = None

    try:
        status = int(match["status"])
    except ValueError:
        return None

    return LogRecord(
        timestamp=timestamp,
        method=method,
        route=route,
        status=status,
        bytes_sent=bytes_sent,
        request_time=request_time,
    )


# --------------------------------------------------------------------------
# File discovery and streaming
# --------------------------------------------------------------------------


def _is_gzip(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(2) == _GZIP_MAGIC
    except OSError:
        return False


def discover_log_files(log_dir: Path) -> list[Path]:
    """Find log files in *log_dir*, sorted by name.

    One file per day is assumed to be named so that lexical order matches
    chronological order (e.g. ``access-2024-01-01.log``); sorting by name
    keeps the stream close to time order without needing to inspect content
    up front.
    """
    files = [p for p in log_dir.iterdir() if p.is_file() and not p.name.startswith(".")]
    return sorted(files, key=lambda p: p.name)


def iter_lines(path: Path) -> Iterator[str]:
    """Yield lines from a plain-text or gzip log file, one at a time."""
    if _is_gzip(path):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            yield from fh
    else:
        with path.open("rt", encoding="utf-8", errors="replace") as fh:
            yield from fh


def iter_records(log_dir: Path) -> Iterator[tuple[LogRecord | None, str]]:
    """Stream-parse every file in *log_dir*, yielding (record_or_none, raw_line).

    Files are processed one at a time and each is read lazily, so memory use
    stays proportional to a single line, never a whole file.
    """
    for path in discover_log_files(log_dir):
        for raw_line in iter_lines(path):
            yield parse_line(raw_line), raw_line


# --------------------------------------------------------------------------
# Percentiles and reservoir sampling
# --------------------------------------------------------------------------


def percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolation percentile of an already-sorted sequence."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * (pct / 100)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[int(rank)]
    lower_val = sorted_values[lower] * (upper - rank)
    upper_val = sorted_values[upper] * (rank - lower)
    return lower_val + upper_val


class ReservoirSampler:
    """Fixed-capacity uniform sample, for bounded-memory global estimates.

    The per-window percentiles computed elsewhere in this module are always
    exact (a window's worth of latencies is small and short-lived). The
    *global* percentiles quoted in the run summary would otherwise require
    holding every latency from every file in memory at once, which is exactly
    what streaming parsing is meant to avoid -- so those are estimated from a
    bounded random sample instead.
    """

    def __init__(self, capacity: int = RESERVOIR_CAPACITY) -> None:
        self.capacity = capacity
        self._sample: list[float] = []
        self._seen = 0

    def add(self, value: float) -> None:
        self._seen += 1
        if len(self._sample) < self.capacity:
            self._sample.append(value)
        else:
            index = random.randint(0, self._seen - 1)
            if index < self.capacity:
                self._sample[index] = value

    def percentiles(self) -> tuple[float, float, float]:
        values = sorted(self._sample)
        return (
            percentile(values, 50),
            percentile(values, 95),
            percentile(values, 99),
        )


# --------------------------------------------------------------------------
# Per-window and per-route accumulation
# --------------------------------------------------------------------------


@dataclass
class _RouteAccumulator:
    count: int = 0
    errors: int = 0
    bytes_total: int = 0
    latencies: list[float] = field(default_factory=list)

    def add(self, record: LogRecord) -> None:
        self.count += 1
        self.bytes_total += record.bytes_sent
        if record.is_error:
            self.errors += 1
        if record.request_time is not None:
            self.latencies.append(record.request_time)


@dataclass
class RouteStats:
    route: str
    count: int
    errors: int
    error_rate: float
    bytes_total: int
    p99: float

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "count": self.count,
            "errors": self.errors,
            "error_rate": round(self.error_rate, 6),
            "bytes_total": self.bytes_total,
            "p99": round(self.p99, 6),
        }


@dataclass
class _WindowAccumulator:
    start: datetime
    count: int = 0
    errors: int = 0
    bytes_total: int = 0
    latencies: list[float] = field(default_factory=list)
    routes: dict[str, _RouteAccumulator] = field(default_factory=dict)

    def add(self, record: LogRecord) -> None:
        self.count += 1
        self.bytes_total += record.bytes_sent
        if record.is_error:
            self.errors += 1
        if record.request_time is not None:
            self.latencies.append(record.request_time)
        self.routes.setdefault(record.route, _RouteAccumulator()).add(record)


@dataclass
class WindowStats:
    start: datetime
    count: int
    error_count: int
    error_rate: float
    p50: float
    p95: float
    p99: float
    bytes_total: int
    is_anomalous: bool
    anomaly_reasons: list[str]
    top_offenders: list[RouteStats]

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "count": self.count,
            "error_count": self.error_count,
            "error_rate": round(self.error_rate, 6),
            "p50": round(self.p50, 6),
            "p95": round(self.p95, 6),
            "p99": round(self.p99, 6),
            "bytes_total": self.bytes_total,
            "is_anomalous": self.is_anomalous,
            "anomaly_reasons": self.anomaly_reasons,
            "top_offenders": [o.to_dict() for o in self.top_offenders],
        }


def _route_stats(route: str, acc: _RouteAccumulator) -> RouteStats:
    error_rate = acc.errors / acc.count if acc.count else 0.0
    sorted_latencies = sorted(acc.latencies)
    return RouteStats(
        route=route,
        count=acc.count,
        errors=acc.errors,
        error_rate=error_rate,
        bytes_total=acc.bytes_total,
        p99=percentile(sorted_latencies, 99),
    )


# --------------------------------------------------------------------------
# Streaming analyser
# --------------------------------------------------------------------------


class AccessLogAnalyzer:
    """Buckets records into one-minute windows and flags anomalous ones.

    Windows are finalised (and can be flagged) once the analyser has seen
    enough later traffic to be confident no more records will land in them --
    a small "grace period" -- rather than assuming perfectly ordered input.
    Trailing-hour history used for anomaly detection is bounded to the last
    ``TRAILING_WINDOWS`` finalised windows, so memory use does not grow with
    the length of the run.
    """

    def __init__(
        self,
        top_n: int = DEFAULT_TOP_N,
        min_history: int = MIN_HISTORY_WINDOWS,
    ) -> None:
        self.top_n = top_n
        self.min_history = min_history

        self._open_windows: dict[datetime, _WindowAccumulator] = {}
        self._max_window: datetime | None = None

        self._trailing_error_rates: deque[float] = deque(maxlen=TRAILING_WINDOWS)
        self._trailing_latencies: deque[list[float]] = deque(maxlen=TRAILING_WINDOWS)

        self.windows: list[WindowStats] = []
        self.total_count = 0
        self.total_errors = 0
        self.total_bytes = 0
        self.malformed_lines = 0
        self.global_routes: dict[str, _RouteAccumulator] = {}
        self._latency_sample = ReservoirSampler()

    @staticmethod
    def _floor_to_window(timestamp: datetime) -> datetime:
        seconds_into_day = (
            timestamp.hour * 3600 + timestamp.minute * 60 + timestamp.second
        )
        floored = seconds_into_day - (seconds_into_day % WINDOW_SECONDS)
        return timestamp.replace(
            hour=floored // 3600,
            minute=(floored % 3600) // 60,
            second=floored % 60,
            microsecond=0,
        )

    def ingest(self, record: LogRecord) -> None:
        self.total_count += 1
        self.total_bytes += record.bytes_sent
        if record.is_error:
            self.total_errors += 1
        if record.request_time is not None:
            self._latency_sample.add(record.request_time)
        self.global_routes.setdefault(record.route, _RouteAccumulator()).add(record)

        window_start = self._floor_to_window(record.timestamp)
        self._max_window = (
            window_start
            if self._max_window is None
            else max(self._max_window, window_start)
        )
        self._open_windows.setdefault(window_start, _WindowAccumulator(window_start)).add(
            record
        )
        self._finalize_ready_windows()

    def _finalize_ready_windows(self) -> None:
        assert self._max_window is not None
        threshold = self._max_window - timedelta(seconds=WINDOW_SECONDS * GRACE_WINDOWS)
        ready = sorted(ws for ws in self._open_windows if ws <= threshold)
        for window_start in ready:
            self._finalize(self._open_windows.pop(window_start))

    def flush(self) -> None:
        """Finalise every window still open. Call once after the last record."""
        for window_start in sorted(self._open_windows):
            self._finalize(self._open_windows.pop(window_start))

    def _finalize(self, acc: _WindowAccumulator) -> None:
        sorted_latencies = sorted(acc.latencies)
        error_rate = acc.errors / acc.count if acc.count else 0.0
        p50 = percentile(sorted_latencies, 50)
        p95 = percentile(sorted_latencies, 95)
        p99 = percentile(sorted_latencies, 99)

        reasons = self._anomaly_reasons(error_rate, p99)
        route_stats = [
            _route_stats(route, route_acc) for route, route_acc in acc.routes.items()
        ]
        top_offenders: list[RouteStats] = []
        if reasons:
            route_stats.sort(key=lambda r: (r.errors, r.p99), reverse=True)
            top_offenders = route_stats[: self.top_n]

        stats = WindowStats(
            start=acc.start,
            count=acc.count,
            error_count=acc.errors,
            error_rate=error_rate,
            p50=p50,
            p95=p95,
            p99=p99,
            bytes_total=acc.bytes_total,
            is_anomalous=bool(reasons),
            anomaly_reasons=reasons,
            top_offenders=top_offenders,
        )
        self.windows.append(stats)

        # Roll this window into trailing history *after* using the history
        # to judge it, so a window is never compared against itself.
        self._trailing_error_rates.append(error_rate)
        self._trailing_latencies.append(sorted_latencies)

    def _anomaly_reasons(self, error_rate: float, p99: float) -> list[str]:
        reasons: list[str] = []

        if len(self._trailing_error_rates) >= self.min_history:
            mean_error = statistics.mean(self._trailing_error_rates)
            std_error = statistics.pstdev(self._trailing_error_rates)
            if error_rate > mean_error + ERROR_STD_THRESHOLD * std_error:
                reasons.append(
                    f"error_rate={error_rate:.4f} exceeds trailing-hour mean "
                    f"{mean_error:.4f} + {ERROR_STD_THRESHOLD:g} sigma "
                    f"({std_error:.4f})"
                )

        if len(self._trailing_latencies) >= self.min_history:
            pooled = sorted(v for window in self._trailing_latencies for v in window)
            trailing_p99 = percentile(pooled, 99)
            if trailing_p99 > 0 and p99 > LATENCY_MULTIPLIER * trailing_p99:
                reasons.append(
                    f"p99={p99:.4f}s exceeds {LATENCY_MULTIPLIER:g}x trailing-hour "
                    f"p99 ({trailing_p99:.4f}s)"
                )

        return reasons

    # -- summary -----------------------------------------------------------

    def summary(self) -> dict:
        overall_error_rate = self.total_errors / self.total_count if self.total_count else 0.0
        p50, p95, p99 = self._latency_sample.percentiles()

        top_by_count = sorted(
            self.global_routes.items(), key=lambda kv: kv[1].count, reverse=True
        )[: self.top_n]
        top_by_errors = sorted(
            (kv for kv in self.global_routes.items() if kv[1].errors),
            key=lambda kv: kv[1].errors,
            reverse=True,
        )[: self.top_n]

        return {
            "total_requests": self.total_count,
            "total_errors": self.total_errors,
            "overall_error_rate": overall_error_rate,
            "total_bytes": self.total_bytes,
            "malformed_lines": self.malformed_lines,
            "windows_analyzed": len(self.windows),
            "anomalous_windows": sum(1 for w in self.windows if w.is_anomalous),
            "latency_p50_estimate": p50,
            "latency_p95_estimate": p95,
            "latency_p99_estimate": p99,
            "top_routes_by_count": [
                {"route": route, "count": acc.count} for route, acc in top_by_count
            ],
            "top_routes_by_errors": [
                {"route": route, "errors": acc.errors} for route, acc in top_by_errors
            ],
        }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def print_report(analyzer: AccessLogAnalyzer) -> None:
    summary = analyzer.summary()

    print("=" * 72)
    print("Access log analysis summary")
    print("=" * 72)
    print(f"Requests analysed:     {summary['total_requests']}")
    print(f"Malformed lines:       {summary['malformed_lines']}")
    print(f"Errors (5xx):          {summary['total_errors']}")
    print(f"Overall error rate:    {summary['overall_error_rate']:.4%}")
    print(f"Bytes served:          {summary['total_bytes']}")
    print(
        "Latency p50/p95/p99 (sampled estimate): "
        f"{summary['latency_p50_estimate']:.4f}s / "
        f"{summary['latency_p95_estimate']:.4f}s / "
        f"{summary['latency_p99_estimate']:.4f}s"
    )
    print(f"One-minute windows:    {summary['windows_analyzed']}")
    print(f"Anomalous windows:     {summary['anomalous_windows']}")

    print("\nTop routes by request count:")
    for entry in summary["top_routes_by_count"]:
        print(f"  {entry['count']:>8}  {entry['route']}")

    if summary["top_routes_by_errors"]:
        print("\nTop routes by error count:")
        for entry in summary["top_routes_by_errors"]:
            print(f"  {entry['errors']:>8}  {entry['route']}")

    anomalous = [w for w in analyzer.windows if w.is_anomalous]
    if anomalous:
        print("\nFlagged windows:")
        for window in anomalous:
            print(f"\n  [{window.start.isoformat()}]")
            print(
                f"    requests={window.count} errors={window.error_count} "
                f"error_rate={window.error_rate:.4%} p99={window.p99:.4f}s"
            )
            for reason in window.anomaly_reasons:
                print(f"    - {reason}")
            if window.top_offenders:
                print("    top offending routes:")
                for offender in window.top_offenders:
                    print(
                        f"      {offender.route}: count={offender.count} "
                        f"errors={offender.errors} p99={offender.p99:.4f}s"
                    )
    else:
        print("\nNo anomalous windows detected.")


def write_json_series(analyzer: AccessLogAnalyzer, path: Path) -> None:
    payload = {
        "summary": analyzer.summary(),
        "windows": [w.to_dict() for w in analyzer.windows],
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def run(
    log_dir: Path,
    json_path: Path | None = None,
    top_n: int = DEFAULT_TOP_N,
    min_history: int = MIN_HISTORY_WINDOWS,
) -> AccessLogAnalyzer:
    analyzer = AccessLogAnalyzer(top_n=top_n, min_history=min_history)
    for record, _raw_line in iter_records(log_dir):
        if record is None:
            analyzer.malformed_lines += 1
            continue
        analyzer.ingest(record)
    analyzer.flush()

    print_report(analyzer)
    if json_path is not None:
        write_json_series(analyzer, json_path)
        print(f"\nPer-window series written to {json_path}")

    return analyzer


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stream-parse a directory of nginx access logs (plain or gzip), "
            "bucket requests into one-minute windows per normalised route, "
            "and flag anomalous windows by error rate and p99 latency."
        )
    )
    parser.add_argument(
        "log_dir",
        type=Path,
        help="Directory containing one access log file per day",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write the full per-window series (and summary) as JSON to PATH",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Number of offending routes to report per flagged window (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--min-history",
        type=int,
        default=MIN_HISTORY_WINDOWS,
        help=(
            "Minimum number of prior windows required before anomaly detection "
            f"activates (default: {MIN_HISTORY_WINDOWS})"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.log_dir.is_dir():
        parser.error(f"{args.log_dir} is not a directory")

    run(
        log_dir=args.log_dir,
        json_path=args.json,
        top_n=args.top_n,
        min_history=args.min_history,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
