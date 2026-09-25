"""How a check is entered: ``check(scan)``, or the deprecated ``run(*, src_root)``.

The :class:`~lanorme.Check` protocol's entry point is ``check(self, scan)``,
which receives the :class:`~lanorme.scan.Scan` for the pass. A check written
against the previous protocol defines ``run(self, *, src_root)`` instead;
:func:`invoke_check` still calls it, with the scan active and a
``DeprecationWarning`` once per check class, until that entry point is removed.
"""

from __future__ import annotations

import warnings
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from lanorme.scan import Scan, get_current_scan

if TYPE_CHECKING:
    from lanorme import Check, CheckResult

# Check classes already told that ``run(*, src_root)`` is deprecated: a run
# over many regions warns once per class rather than once per call.
_legacy_run_warned: set[type] = set()


def resolve_scan(*, scan: Scan | None, src_root: str | None) -> Scan:
    """The scan to run over: *scan* when given, else the current one re-rooted at *src_root*.

    A bare *src_root* keeps the confinement in force (the scope, the exclude
    globs, the ``source_root``) and the run's parse cache, so a caller that
    passes a path inside a run prunes what the run prunes; outside a run that
    is the whole tree with no excludes.
    """
    if scan is not None:
        if not isinstance(scan, Scan):
            raise TypeError(f"scan= must be a lanorme.scan.Scan, not {type(scan).__name__}")
        return scan
    if src_root is None:
        raise TypeError("pass scan= (a lanorme.scan.Scan) or src_root= (a path)")
    return replace(get_current_scan(), root=Path(src_root))


def invoke_check(check: Check, *, scan: Scan) -> CheckResult:
    """Call the entry point of *check* over *scan*, letting any exception escape.

    ``check(scan)`` when the check defines it, else the deprecated
    ``run(src_root=...)``. The scan is active for the call either way, so
    ``lanorme.sources`` and ``lanorme.discovery`` prune what it prunes.
    """
    entry = getattr(check, "check", None)
    if not callable(entry):
        warn_legacy_run_once(check)
    with scan.activate():
        if callable(entry):
            return entry(scan)
        return check.run(src_root=str(scan.root))  # type: ignore[attr-defined]


def warn_legacy_run_once(check: Check) -> None:
    """Say once per class that ``run(*, src_root)`` is deprecated.

    Called before a run is isolated, so a ``-W error::DeprecationWarning``
    reaches the caller as the error it asked for instead of a crash notice
    on the check, and the class is remembered only once the warning was
    delivered.
    """
    check_type = type(check)
    if check_type in _legacy_run_warned or callable(getattr(check, "check", None)):
        return
    warnings.warn(
        f"{check_type.__module__}.{check_type.__qualname__} defines run(*, src_root) "
        "but no check(scan): the run() entry point is deprecated and will be removed; "
        "implement check(self, scan: lanorme.scan.Scan) -> CheckResult",
        DeprecationWarning,
        stacklevel=4,
    )
    _legacy_run_warned.add(check_type)
