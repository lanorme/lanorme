"""The feature store: register features, version them, materialise and
backfill them, and query them point-in-time correctly.

This is the module most callers use directly; it wires together
:mod:`featurestore.registry` (bookkeeping, including versions and backfill
progress), :mod:`featurestore.materialize` (computing feature tables from
source tables), :mod:`featurestore.backfill` (chunking a historical range),
and the point-in-time lookup below (answering "what did we know, and when,
under which version").
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from featurestore.backfill import BackfillResult, chunk_windows, is_covered
from featurestore.errors import MissingTimestampError, UnknownColumnError
from featurestore.feature import Feature, Row
from featurestore.materialize import FeatureRow, compute_feature_rows
from featurestore.registry import BackfillReport, FreshnessReport, Registry
from featurestore.versioning import FeatureVersion


class FeatureStore:
    """A small, in-memory, point-in-time correct, versioned feature store."""

    def __init__(self) -> None:
        self.registry = Registry()

    # -- declaration and versioning -----------------------------------------

    def register(self, feature: Feature, *, effective_from: Any = None) -> None:
        """Declare a brand new feature at version 1. Rejects a duplicate
        feature name -- use :meth:`add_version` to change an existing
        feature's transform (or anything else about its declaration).
        """
        self.registry.register(feature, effective_from=effective_from)

    def add_version(self, feature: Feature, *, effective_from: Any = None) -> int:
        """Add a new version of the feature named ``feature.name``, which
        must already be registered.

        This is how a transform (or entity, dtype, owner, ...) changes:
        never by mutating the existing declaration, always by adding a new
        version. Both the old and the new version keep their own
        materialised feature table and stay independently queryable --
        including by a point-in-time query whose ``as_of`` falls before
        the new version's ``effective_from``, which still sees the old one.

        ``effective_from`` defaults to now, and must not precede the
        current latest version's ``effective_from``. Returns the new
        version number.
        """
        return self.registry.add_version(feature, effective_from=effective_from).version

    def list_versions(self, feature_name: str) -> list[FeatureVersion]:
        """Every version of ``feature_name``, oldest first."""
        return self.registry.list_versions(feature_name)

    def get_version(self, feature_name: str, version: int) -> FeatureVersion:
        """Look up one specific version of a registered feature."""
        return self.registry.get_version(feature_name, version)

    # -- materialisation --------------------------------------------------

    def materialize(self, feature_name: str, source_table: list[Row], *, version: int | None = None) -> int:
        """Materialise ``feature_name`` from ``source_table``, defaulting to
        its latest version.

        Computes the feature for every row of ``source_table`` and merges
        the results into that version's feature table, upserting by
        (entity, event timestamp) so materialising the same source rows
        twice is a no-op and materialising new rows is additive -- the
        feature table accumulates history rather than being replaced. A
        different version of the same feature has its own, separate
        feature table: materialising one never touches another's data.

        Rejects a transform that reads a column absent from the source
        table (raises :class:`UnknownColumnError`), including the declared
        entity key and timestamp columns.

        Returns the number of rows now in that version's feature table.
        """
        feature_version = self._resolve_version(feature_name, version)
        new_rows = compute_feature_rows(feature_version.feature, source_table)
        merged = _upsert(self.registry.materialized_rows(feature_name, feature_version.version), new_rows)
        self.registry.set_materialized_rows(feature_name, feature_version.version, merged)
        return len(merged)

    def materialize_all(self, source_tables: Mapping[str, list[Row]]) -> dict[str, int]:
        """Materialise the latest version of every registered feature whose
        source table is provided.

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

    # -- backfill -------------------------------------------------------------

    def backfill(
        self,
        feature_name: str,
        source_table: list[Row],
        start: Any,
        end: Any,
        chunk_size: Any,
        *,
        version: int | None = None,
    ) -> BackfillResult:
        """Backfill ``feature_name`` (defaulting to its latest version) over
        the historical range ``[start, end)``, in chunks.

        Splits the range into consecutive windows of ``chunk_size`` (a
        value that can be added to a timestamp -- a ``timedelta`` for
        ``datetime`` timestamps, or a plain number for integer ones). For
        each window, computes and upserts the feature rows for the
        ``source_table`` rows whose ``timestamp_column`` falls in that
        window, then records the window as complete before moving to the
        next.

        A window already covered by a prior, completed backfill call is
        skipped outright rather than recomputed. Even so, the upsert on
        write is itself idempotent by (entity, timestamp), so retrying a
        window -- whole or in part -- never leaves a duplicate row: this
        holds two ways against double-writing.

        Windows are committed one at a time -- upsert, then mark complete
        -- so if a window's computation raises (for example
        :class:`~featurestore.errors.UnknownColumnError` on a bad row),
        every earlier window in this call is already durably recorded as
        done. Calling ``backfill`` again with the same arguments resumes
        from the first incomplete window rather than redoing the ones
        already committed -- that is what makes an interrupted backfill
        resumable.
        """
        feature_version = self._resolve_version(feature_name, version)
        ts_column = feature_version.feature.timestamp_column
        windows_run = 0
        windows_skipped = 0
        rows_upserted = 0
        for window_start, window_end in chunk_windows(start, end, chunk_size):
            completed = self.registry.completed_ranges(feature_name, feature_version.version)
            if is_covered((window_start, window_end), completed):
                windows_skipped += 1
                continue
            window_rows = _rows_in_window(source_table, ts_column, window_start, window_end)
            new_rows = compute_feature_rows(feature_version.feature, window_rows)
            existing = self.registry.materialized_rows(feature_name, feature_version.version)
            self.registry.set_materialized_rows(feature_name, feature_version.version, _upsert(existing, new_rows))
            self.registry.mark_range_completed(feature_name, feature_version.version, window_start, window_end)
            windows_run += 1
            rows_upserted += len(new_rows)
        return BackfillResult(
            feature_name=feature_name,
            version=feature_version.version,
            windows_run=windows_run,
            windows_skipped=windows_skipped,
            rows_upserted=rows_upserted,
            completed_ranges=self.registry.completed_ranges(feature_name, feature_version.version),
        )

    def backfill_report(self, feature_name: str | None = None) -> list[BackfillReport]:
        """Which versions of which features have been backfilled, and over
        which ranges. Restricts to ``feature_name`` if given.
        """
        return self.registry.backfill_report(feature_name)

    # -- point-in-time queries -------------------------------------------

    def get_features(
        self,
        entity_value: Any,
        as_of: Any = None,
        feature_names: Sequence[str] | None = None,
        feature_versions: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        """Point-in-time lookup for a single entity at a single timestamp.

        Returns, for each requested feature, the value materialised for
        ``entity_value`` with the latest ``event_timestamp <= as_of`` --
        never one known only later. A feature with no such history yields
        ``None``. Rejects a query made with no ``as_of`` timestamp.

        For each feature, the version consulted is the one that was
        current ``as_of`` -- the version with the greatest ``effective_from``
        at or before ``as_of`` -- unless ``feature_versions`` maps that
        feature's name to an explicit version number, which is used
        instead regardless of its ``effective_from``.

        ``feature_names`` defaults to every registered feature.
        """
        if as_of is None:
            raise MissingTimestampError("a point-in-time query requires an 'as_of' event timestamp")
        names = feature_names if feature_names is not None else [f.name for f in self.registry.list_features()]
        versions = feature_versions or {}
        return {name: self._as_of(name, entity_value, as_of, versions.get(name)) for name in names}

    def get_historical_features(
        self,
        entity_rows: Sequence[Mapping[str, Any]],
        feature_names: Sequence[str],
        *,
        entity_column: str,
        timestamp_column: str = "event_timestamp",
        feature_versions: Mapping[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Point-in-time correct join of many entity rows against features.

        For each row in ``entity_rows`` (each holding an entity id under
        ``entity_column`` and an event timestamp under ``timestamp_column``),
        looks up every feature in ``feature_names`` as of that row's own
        timestamp, and returns new rows -- copies of the input rows plus one
        column per feature -- so no feature value ever leaks from after the
        row's own point in time. This is the join used to build a training
        set without cheating with future data.

        Each feature's version is pinned per row by that row's own
        timestamp (the version current at that moment), unless
        ``feature_versions`` maps that feature's name to an explicit
        version, which then applies to every row in this call.

        Rejects an entity row with no timestamp, and an entity row missing
        ``entity_column`` or ``timestamp_column`` entirely.
        """
        versions = feature_versions or {}
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
                enriched[name] = self._as_of(name, entity_value, as_of, versions.get(name))
            joined.append(enriched)
        return joined

    # -- registry passthroughs --------------------------------------------

    def list_features(self) -> list[Feature]:
        """The current (latest version's) declaration of every registered
        feature, sorted by name.
        """
        return self.registry.list_features()

    def freshness(self, feature_name: str, version: int | None = None) -> FreshnessReport:
        """Owner, row count, and freshness for one feature version, defaulting
        to its latest version.
        """
        return self.registry.freshness(feature_name, version)

    def freshness_report(self) -> list[FreshnessReport]:
        """Owner, row count, and freshness for every registered feature's
        latest version.
        """
        return self.registry.freshness_report()

    # -- internals ----------------------------------------------------------

    def _resolve_version(self, feature_name: str, version: int | None) -> FeatureVersion:
        if version is not None:
            return self.registry.get_version(feature_name, version)
        return self.registry.latest_version(feature_name)

    def _as_of(self, feature_name: str, entity_value: Any, as_of: Any, version: int | None) -> Any:
        feature_version = (
            self.registry.get_version(feature_name, version)
            if version is not None
            else self.registry.version_at(feature_name, as_of)
        )
        if feature_version is None:
            return None
        rows = self.registry.materialized_rows(feature_name, feature_version.version)
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


def _rows_in_window(source_table: list[Row], ts_column: str, start: Any, end: Any) -> list[Row]:
    """The rows of ``source_table`` whose ``ts_column`` falls in ``[start, end)``.

    Raises :class:`UnknownColumnError` the moment any row lacks
    ``ts_column`` entirely, rather than silently excluding it -- a row a
    backfill cannot place in any window is a data problem, not an empty
    window.
    """
    in_window = []
    for index, row in enumerate(source_table):
        if ts_column not in row:
            raise UnknownColumnError(
                f"backfill needs the timestamp column '{ts_column}' on every source row, "
                f"but row {index} does not have it"
            )
        if start <= row[ts_column] < end:
            in_window.append(row)
    return in_window


def _required_row_column(row: Mapping[str, Any], column: str, index: int, *, role: str) -> Any:
    if column not in row:
        raise UnknownColumnError(f"entity row {index} has no {role} column '{column}'")
    return row[column]
