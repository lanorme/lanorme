"""The feature registry: what features exist, who owns them, how fresh they are.

The registry is the source of truth for declared features and for the
feature table each one has been materialised into. It does not know how to
compute a feature (that is :mod:`featurestore.materialize`'s job) or how to
run a point-in-time query (that is :class:`featurestore.store.FeatureStore`'s
job) -- it only stores and reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from featurestore.errors import DuplicateFeatureError, UnknownFeatureError
from featurestore.feature import Feature
from featurestore.materialize import FeatureRow


@dataclass
class _Entry:
    feature: Feature
    materialized_rows: list[FeatureRow] = field(default_factory=list)
    last_materialized_at: datetime | None = None


@dataclass(frozen=True)
class FreshnessReport:
    """A snapshot of how fresh one feature's materialised data is.

    ``last_materialized_at`` is wall-clock time: when ``materialize`` was
    last run for this feature. ``latest_event_timestamp`` is data time: the
    most recent event timestamp among the materialised rows, i.e. how
    recent the underlying facts are, not how recently the job ran.
    """

    name: str
    owner: str
    row_count: int
    is_materialized: bool
    last_materialized_at: datetime | None
    latest_event_timestamp: Any


class Registry:
    """Tracks every declared feature, its owner, and its materialised data."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    def register(self, feature: Feature) -> None:
        """Declare a feature. Rejects a name that is already registered."""
        if feature.name in self._entries:
            raise DuplicateFeatureError(f"a feature named '{feature.name}' is already registered")
        self._entries[feature.name] = _Entry(feature=feature)

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def get(self, name: str) -> Feature:
        """Look up a registered feature's declaration by name."""
        return self._entry(name).feature

    def list_features(self) -> list[Feature]:
        """All registered features, sorted by name."""
        return [entry.feature for entry in sorted(self._entries.values(), key=lambda e: e.feature.name)]

    def set_materialized_rows(self, name: str, rows: list[FeatureRow]) -> None:
        entry = self._entry(name)
        entry.materialized_rows = rows
        entry.last_materialized_at = datetime.now(timezone.utc)

    def materialized_rows(self, name: str) -> list[FeatureRow]:
        return self._entry(name).materialized_rows

    def freshness(self, name: str) -> FreshnessReport:
        """Owner, row count, and freshness for one feature."""
        entry = self._entry(name)
        rows = entry.materialized_rows
        latest_event_timestamp = max((r["event_timestamp"] for r in rows), default=None)
        return FreshnessReport(
            name=entry.feature.name,
            owner=entry.feature.owner,
            row_count=len(rows),
            is_materialized=entry.last_materialized_at is not None,
            last_materialized_at=entry.last_materialized_at,
            latest_event_timestamp=latest_event_timestamp,
        )

    def freshness_report(self) -> list[FreshnessReport]:
        """Owner, row count, and freshness for every registered feature."""
        return [self.freshness(feature.name) for feature in self.list_features()]

    def _entry(self, name: str) -> _Entry:
        try:
            return self._entries[name]
        except KeyError:
            raise UnknownFeatureError(f"no feature named '{name}' is registered") from None
