"""Training-set snapshots: freezing a point-in-time join into a reproducible,
content-addressed dataset, plus later checking whether it still reproduces.

:func:`snapshot_training_set` runs the same point-in-time join as
:meth:`featurestore.store.FeatureStore.get_historical_features`, but instead
of just returning the joined rows it:

* pins the exact feature version that answered each (row, feature) pair,
  rather than leaving it implicit in "whatever ``version_at`` picks today";
* records, for every feature version touched -- directly requested or
  reached through :mod:`featurestore.lineage` -- its source table and
  upstream feature versions, transitively (the manifest);
* freezes the resulting dataset (read-only rows) and content-addresses it
  with a hash of its exact contents.

:func:`check_reproducible` takes that snapshot back to a (possibly later,
possibly different) :class:`~featurestore.registry.Registry` and asks: if I
re-run the same pinned lookups now, do I get byte-identical values? A
feature store never deletes or overwrites a *version*'s declaration, but it
can accumulate more materialised rows for that version over time (a
correction to a previously-materialised value, upserted by the same entity
and event timestamp), which can silently change a historical point-in-time
answer. That is exactly what this catches, and names precisely.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from featurestore.errors import MissingTimestampError, UnknownColumnError, UnknownFeatureError, UnknownVersionError
from featurestore.lineage import transitive_closure
from featurestore.registry import Registry
from featurestore.versioning import FeatureVersion

#: A single frozen row of a training-set snapshot: the original entity row's
#: columns plus one column per requested feature. Read-only.
SnapshotRow = Mapping[str, Any]


@dataclass(frozen=True)
class ManifestEntry:
    """Everything the manifest records about one feature version that a
    snapshot touched, directly or through lineage: what it derives from, and
    a fingerprint of its materialised data at snapshot time.
    """

    feature_name: str
    version: int
    source_tables: frozenset[str]
    upstream_feature_versions: frozenset[tuple[str, int]]
    materialized_fingerprint: str


@dataclass(frozen=True)
class SnapshotManifest:
    """Everything needed to explain, and later re-check, one training-set
    snapshot: the request that produced it, which version answered each
    (row, feature) pair, and the lineage plus data fingerprint of every
    feature version involved.
    """

    entity_column: str
    timestamp_column: str
    feature_names: tuple[str, ...]
    row_count: int
    created_at: datetime
    per_row_versions: tuple[Mapping[str, int | None], ...]
    entries: Mapping[tuple[str, int], ManifestEntry]


@dataclass(frozen=True)
class TrainingSetSnapshot:
    """A frozen, content-addressed training set plus its manifest.

    ``content_hash`` addresses the dataset's exact contents (the request
    shape and every row and column value): two snapshots built from the
    same inputs against the same store state hash identically.
    """

    content_hash: str
    dataset: tuple[SnapshotRow, ...]
    manifest: SnapshotManifest


@dataclass(frozen=True)
class ChangedInput:
    """One (row, feature) pair whose value at snapshot time no longer
    matches what re-resolving it against current state yields.
    """

    row_index: int
    feature_name: str
    entity_value: Any
    as_of: Any
    recorded_value: Any
    current_value: Any


@dataclass(frozen=True)
class ReproducibilityCheck:
    """The outcome of checking whether a snapshot still reproduces."""

    reproducible: bool
    content_hash_matches: bool
    changed_inputs: tuple[ChangedInput, ...]
    reason: str


def snapshot_training_set(
    registry: Registry,
    entity_rows: Sequence[Mapping[str, Any]],
    feature_names: Sequence[str],
    *,
    entity_column: str,
    timestamp_column: str = "event_timestamp",
) -> TrainingSetSnapshot:
    """Freeze a point-in-time join of ``entity_rows`` against ``feature_names``
    into a reproducible training-set snapshot.

    Each row of ``entity_rows`` must carry an entity id under
    ``entity_column`` and an event timestamp under ``timestamp_column`` (as
    for :meth:`featurestore.store.FeatureStore.get_historical_features`);
    rejects a row missing either, or with no timestamp set. Every feature in
    ``feature_names`` is resolved as of that row's own timestamp, pinning
    the exact version that answered so the manifest -- and a later
    reproducibility check -- do not depend on version resolution being
    stable over time, only on the pinned version's materialised data being
    stable.
    """
    dataset_rows: list[SnapshotRow] = []
    per_row_versions: list[Mapping[str, int | None]] = []
    touched: dict[tuple[str, int], FeatureVersion] = {}

    for index, row in enumerate(entity_rows):
        as_of = _required(row, timestamp_column, index, role="timestamp")
        if as_of is None:
            raise MissingTimestampError(
                f"a training-set snapshot requires every row to have '{timestamp_column}' "
                f"set, but row {index} has none"
            )
        entity_value = _required(row, entity_column, index, role="entity key")
        enriched = dict(row)
        row_versions: dict[str, int | None] = {}
        for name in feature_names:
            value, version = _resolve(registry, name, entity_value, as_of)
            enriched[name] = value
            row_versions[name] = version
            if version is not None:
                touched[(name, version)] = registry.get_version(name, version)
        dataset_rows.append(MappingProxyType(enriched))
        per_row_versions.append(MappingProxyType(row_versions))

    manifest = SnapshotManifest(
        entity_column=entity_column,
        timestamp_column=timestamp_column,
        feature_names=tuple(feature_names),
        row_count=len(dataset_rows),
        created_at=datetime.now(timezone.utc),
        per_row_versions=tuple(per_row_versions),
        entries=MappingProxyType(_build_entries(registry, touched)),
    )
    content_hash = _dataset_hash(entity_column, timestamp_column, feature_names, dataset_rows)
    return TrainingSetSnapshot(content_hash=content_hash, dataset=tuple(dataset_rows), manifest=manifest)


def check_reproducible(registry: Registry, snapshot: TrainingSetSnapshot) -> ReproducibilityCheck:
    """Check whether ``snapshot`` can still be reproduced from ``registry``'s
    current state.

    Re-resolves every (row, feature) pair using the exact version pinned in
    the snapshot's manifest (falling back to a fresh point-in-time lookup
    where no version existed yet at snapshot time), and compares the result
    to the frozen value. If a registered feature or version referenced by
    the manifest has disappeared, that counts as a change too (though
    versions are never actually removed by this store today). Returns
    every mismatch found, plus a human-readable summary of the first one.
    """
    manifest = snapshot.manifest
    changed: list[ChangedInput] = []
    recomputed_rows: list[dict[str, Any]] = []

    for index, row in enumerate(snapshot.dataset):
        as_of = row[manifest.timestamp_column]
        entity_value = row[manifest.entity_column]
        row_versions = manifest.per_row_versions[index]
        recomputed = dict(row)
        for name in manifest.feature_names:
            pinned_version = row_versions[name]
            try:
                value, _ = _resolve(registry, name, entity_value, as_of, version=pinned_version)
            except (UnknownFeatureError, UnknownVersionError) as exc:
                value = f"<unreproducible: {exc}>"
            recomputed[name] = value
            recorded = row[name]
            if value != recorded:
                changed.append(
                    ChangedInput(
                        row_index=index,
                        feature_name=name,
                        entity_value=entity_value,
                        as_of=as_of,
                        recorded_value=recorded,
                        current_value=value,
                    )
                )
        recomputed_rows.append(recomputed)

    new_hash = _dataset_hash(
        manifest.entity_column, manifest.timestamp_column, manifest.feature_names, recomputed_rows
    )
    matches = new_hash == snapshot.content_hash
    if not changed and matches:
        return ReproducibilityCheck(
            reproducible=True,
            content_hash_matches=True,
            changed_inputs=(),
            reason="reproducible: recomputing from current state yields the identical dataset",
        )
    return ReproducibilityCheck(
        reproducible=False,
        content_hash_matches=matches,
        changed_inputs=tuple(changed),
        reason=_describe(changed) if changed else "not reproducible: recomputed dataset differs from the snapshot",
    )


def _describe(changed: list[ChangedInput]) -> str:
    first = changed[0]
    message = (
        f"not reproducible: feature '{first.feature_name}' for entity {first.entity_value!r} "
        f"at {first.as_of!r} (row {first.row_index}) was {first.recorded_value!r} at snapshot "
        f"time but is now {first.current_value!r}"
    )
    if len(changed) > 1:
        message += f" ({len(changed) - 1} more value(s) also changed)"
    return message


def _build_entries(
    registry: Registry, touched: Mapping[tuple[str, int], FeatureVersion]
) -> dict[tuple[str, int], ManifestEntry]:
    """Walk the lineage closure of every directly-touched feature version so
    the manifest covers upstream provenance too, not only the top-level
    features a caller asked for.
    """
    entries: dict[tuple[str, int], ManifestEntry] = {}
    frontier: list[FeatureVersion] = list(touched.values())
    while frontier:
        feature_version = frontier.pop()
        key = (feature_version.feature.name, feature_version.version)
        if key in entries:
            continue
        closure = transitive_closure(feature_version, registry)
        entries[key] = ManifestEntry(
            feature_name=feature_version.feature.name,
            version=feature_version.version,
            source_tables=closure.source_tables,
            upstream_feature_versions=frozenset((fv.feature.name, fv.version) for fv in closure.feature_versions),
            materialized_fingerprint=_rows_fingerprint(
                registry.materialized_rows(feature_version.feature.name, feature_version.version)
            ),
        )
        frontier.extend(closure.feature_versions)
    return entries


def _resolve(
    registry: Registry,
    feature_name: str,
    entity_value: Any,
    as_of: Any,
    *,
    version: int | None = None,
) -> tuple[Any, int | None]:
    """Point-in-time resolve one feature's value for one entity, mirroring
    :meth:`featurestore.store.FeatureStore._as_of`, but also returning which
    version answered -- needed to pin it in a snapshot's manifest.
    """
    feature_version = (
        registry.get_version(feature_name, version) if version is not None else registry.version_at(feature_name, as_of)
    )
    if feature_version is None:
        return None, None
    rows = registry.materialized_rows(feature_name, feature_version.version)
    known_by_now = [r for r in rows if r["entity"] == entity_value and r["event_timestamp"] <= as_of]
    if not known_by_now:
        return None, feature_version.version
    latest = max(known_by_now, key=lambda r: r["event_timestamp"])
    return latest["value"], feature_version.version


def _required(row: Mapping[str, Any], column: str, index: int, *, role: str) -> Any:
    if column not in row:
        raise UnknownColumnError(f"entity row {index} has no {role} column '{column}'")
    return row[column]


def _dataset_hash(
    entity_column: str,
    timestamp_column: str,
    feature_names: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> str:
    payload = {
        "entity_column": entity_column,
        "timestamp_column": timestamp_column,
        "feature_names": list(feature_names),
        "rows": [dict(row) for row in rows],
    }
    return _content_hash(payload)


def _rows_fingerprint(rows: list[Mapping[str, Any]]) -> str:
    ordered = sorted(rows, key=lambda r: (repr(r["entity"]), repr(r["event_timestamp"])))
    return _content_hash(ordered)


def _content_hash(payload: Any) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=_json_default)


def _json_default(value: Any) -> Any:
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return repr(value)
