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

from featurestore.errors import (
    DuplicateFeatureError,
    FeatureStoreError,
    MissingTimestampError,
    UnknownColumnError,
    UnknownFeatureError,
)
from featurestore.feature import Feature, Row, Transform
from featurestore.registry import FreshnessReport, Registry
from featurestore.store import FeatureStore

__all__ = [
    "Feature",
    "FeatureStore",
    "Registry",
    "FreshnessReport",
    "Row",
    "Transform",
    "FeatureStoreError",
    "DuplicateFeatureError",
    "UnknownFeatureError",
    "UnknownColumnError",
    "MissingTimestampError",
]
