"""featurestore: a small, point-in-time correct feature store.

Standard library only. Source tables and feature tables are both plain
``list[dict]``.

Typical use::

    from datetime import datetime, timezone
    from featurestore import Feature, FeatureStore

    def total_spend(row):
        return row["amount"] * row["quantity"]

    orders_feature = Feature(
        name="total_spend",
        entity="user_id",
        dtype=float,
        source="orders",
        transform=total_spend,
        owner="growth-team",
    )

    store = FeatureStore()
    store.register(orders_feature)
    store.materialize("total_spend", orders_table)

    store.get_features(
        entity_value=42,
        as_of=datetime(2024, 3, 1, tzinfo=timezone.utc),
        feature_names=["total_spend"],
    )
"""

from __future__ import annotations

from featurestore.backfill import BackfillResult
from featurestore.errors import (
    DuplicateFeatureError,
    FeatureStoreError,
    InvalidRangeError,
    LineageCycleError,
    MissingTimestampError,
    NonMonotonicVersionError,
    UnknownColumnError,
    UnknownFeatureError,
    UnknownVersionError,
)
from featurestore.feature import Feature, Row, Transform
from featurestore.lineage import Lineage, LineageClosure, direct_lineage, transitive_closure
from featurestore.registry import BackfillReport, FreshnessReport, Registry
from featurestore.snapshot import (
    ChangedInput,
    ManifestEntry,
    ReproducibilityCheck,
    SnapshotManifest,
    TrainingSetSnapshot,
    check_reproducible,
    snapshot_training_set,
)
from featurestore.store import FeatureStore
from featurestore.versioning import GENESIS, FeatureVersion

__all__ = [
    "Feature",
    "FeatureStore",
    "FeatureVersion",
    "GENESIS",
    "Registry",
    "FreshnessReport",
    "BackfillReport",
    "BackfillResult",
    "Row",
    "Transform",
    "FeatureStoreError",
    "DuplicateFeatureError",
    "UnknownFeatureError",
    "UnknownColumnError",
    "MissingTimestampError",
    "UnknownVersionError",
    "NonMonotonicVersionError",
    "InvalidRangeError",
    "LineageCycleError",
    "Lineage",
    "LineageClosure",
    "direct_lineage",
    "transitive_closure",
    "TrainingSetSnapshot",
    "SnapshotManifest",
    "ManifestEntry",
    "ChangedInput",
    "ReproducibilityCheck",
    "snapshot_training_set",
    "check_reproducible",
]
