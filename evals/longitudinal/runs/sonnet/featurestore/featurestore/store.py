"""The feature store: register features, materialise them, and query them
point-in-time correctly.

This is the module most callers use directly; it wires together
:mod:`featurestore.registry` (bookkeeping), :mod:`featurestore.materialize`
(computing feature tables from source tables), and the point-in-time lookup
below (answering "what did we know, and when").
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from featurestore.errors import MissingTimestampError, UnknownColumnError
from featurestore.feature import Feature, Row
from featurestore.materialize import FeatureRow, compute_feature_rows
from featurestore.registry import FreshnessReport, Registry


class FeatureStore:
    """A small, in-memory, point-in-time correct feature store."""

    def __init__(self) -> None:
        self.registry = Registry()

    # -- declaration ----------------------------------------------------

    def register(self, feature: Feature) -> None:
        """Declare a feature. Rejects a duplicate feature name."""
        self.registry.register(feature)

    # -- materialisation --------------------------------------------------

    def materialize(self, feature_name: str, source_table: list[Row]) -> int:
        """Materialise ``feature_name`` from ``source_table``.

        Computes the feature for every row of ``source_table`` and merges
        the results into that feature's feature table, upserting by
        (entity, event timestamp) so materialising the same source rows
        twice is a no-op and materialising new rows is additive -- the
        feature table accumulates history rather than being replaced.

        Rejects a transform that reads a column absent from the source
        table (raises :class:`UnknownColumnError`), including the declared
        entity key and timestamp columns.

        Returns the number of rows now in the feature table.
        """
        feature = self.registry.get(feature_name)
        new_rows = compute_feature_rows(feature, source_table)
        merged = _upsert(self.registry.materialized_rows(feature_name), new_rows)
        self.registry.set_materialized_rows(feature_name, merged)
        return len(merged)

    def materialize_all(self, source_tables: Mapping[str, list[Row]]) -> dict[str, int]:
        """Materialise every registered feature whose source table is provided.

        ``source_tables`` maps a source table name (matching
        ``Feature.source``) to the table itself. A registered feature whose
        declared source is not among ``source_tables`` is skipped rather
        than rejected, so a partial batch of tables can still be applied.
        Returns the new row count for each feature that was materialised.
        """
        counts: dict[str, int] = {}
        for feature in self.registry.list_features():
            if feature.source in source_tables:
                counts[feature.name] = self.materialize(feature.name, source_tables[feature.source])
        return counts

    # -- point-in-time queries -------------------------------------------

    def get_features(
        self,
        entity_value: Any,
        as_of: Any = None,
        feature_names: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Point-in-time lookup for a single entity at a single timestamp.

        Returns, for each requested feature, the value materialised for
        ``entity_value`` with the latest ``event_timestamp <= as_of`` --
        never one known only later. A feature with no such history yields
        ``None``. Rejects a query made with no ``as_of`` timestamp.

        ``feature_names`` defaults to every registered feature.
        """
        if as_of is None:
            raise MissingTimestampError("a point-in-time query requires an 'as_of' event timestamp")
        names = feature_names if feature_names is not None else [f.name for f in self.registry.list_features()]
        return {name: self._as_of(name, entity_value, as_of) for name in names}

    def get_historical_features(
        self,
        entity_rows: Sequence[Mapping[str, Any]],
        feature_names: Sequence[str],
        *,
        entity_column: str,
        timestamp_column: str = "event_timestamp",
    ) -> list[dict[str, Any]]:
        """Point-in-time correct join of many entity rows against features.

        For each row in ``entity_rows`` (each holding an entity id under
        ``entity_column`` and an event timestamp under ``timestamp_column``),
        looks up every feature in ``feature_names`` as of that row's own
        timestamp, and returns new rows -- copies of the input rows plus one
        column per feature -- so no feature value ever leaks from after the
        row's own point in time. This is the join used to build a training
        set without cheating with future data.

        Rejects an entity row with no timestamp, and an entity row missing
        ``entity_column`` or ``timestamp_column`` entirely.
        """
        joined: list[dict[str, Any]] = []
        for index, row in enumerate(entity_rows):
            as_of = _required_row_column(row, timestamp_column, index, role="timestamp")
            if as_of is None:
                raise MissingTimestampError(
                    f"a point-in-time join requires every row to have '{timestamp_column}' "
                    f"set, but row {index} has none"
                )
            entity_value = _required_row_column(row, entity_column, index, role="entity key")
            enriched = dict(row)
            for name in feature_names:
                enriched[name] = self._as_of(name, entity_value, as_of)
            joined.append(enriched)
        return joined

    # -- registry passthroughs --------------------------------------------

    def list_features(self) -> list[Feature]:
        """Every registered feature, sorted by name."""
        return self.registry.list_features()

    def freshness(self, feature_name: str) -> FreshnessReport:
        """Owner, row count, and freshness for one registered feature."""
        return self.registry.freshness(feature_name)

    def freshness_report(self) -> list[FreshnessReport]:
        """Owner, row count, and freshness for every registered feature."""
        return self.registry.freshness_report()

    # -- internals ----------------------------------------------------------

    def _as_of(self, feature_name: str, entity_value: Any, as_of: Any) -> Any:
        self.registry.get(feature_name)  # raises UnknownFeatureError if not registered
        rows = self.registry.materialized_rows(feature_name)
        known_by_now = [r for r in rows if r["entity"] == entity_value and r["event_timestamp"] <= as_of]
        if not known_by_now:
            return None
        latest = max(known_by_now, key=lambda r: r["event_timestamp"])
        return latest["value"]


def _upsert(existing: list[FeatureRow], new: list[FeatureRow]) -> list[FeatureRow]:
    """Merge ``new`` rows into ``existing``, later rows winning on (entity, timestamp)."""
    by_key: dict[tuple[Any, Any], FeatureRow] = {(r["entity"], r["event_timestamp"]): r for r in existing}
    for row in new:
        by_key[(row["entity"], row["event_timestamp"])] = row
    return list(by_key.values())


def _required_row_column(row: Mapping[str, Any], column: str, index: int, *, role: str) -> Any:
    if column not in row:
        raise UnknownColumnError(f"entity row {index} has no {role} column '{column}'")
    return row[column]
