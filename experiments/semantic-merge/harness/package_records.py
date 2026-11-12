"""The record of one measured package, as ``measure_packages.py`` writes it to JSON."""

from __future__ import annotations

from typing import TypedDict


class PackageRecord(TypedDict):
    """One merge candidate: its label, what SHALLOW-001 makes of it, and its signals."""

    identifier: str
    split: str
    lineage: str
    is_shallow: bool
    is_reported: bool
    member_count: int
    lines: int
    note: str
    is_reachable: bool
    is_leaf: bool
    is_repeated_layout: bool
    closes_cycle: bool
    signals: dict[str, float | None]
    reuse_sentence: str
