"""Turning a source table into rows of a feature table.

The feature table for a given feature is stored internally with a fixed,
canonical shape regardless of what the source table's entity/timestamp
columns were called: ``{"entity": ..., "event_timestamp": ..., "value":
...}``. That keeps every downstream lookup (point-in-time queries, joins,
freshness) simple and uniform.
"""

from __future__ import annotations

from typing import Any

from featurestore.errors import UnknownColumnError
from featurestore.feature import Feature, Row

#: One materialised row: the canonical internal shape of a feature table.
FeatureRow = dict[str, Any]


def compute_feature_rows(feature: Feature, source_table: list[Row]) -> list[FeatureRow]:
    """Run ``feature.transform`` over every row of ``source_table``.

    Returns one materialised row per source row. Raises
    :class:`UnknownColumnError` the moment any row is missing the entity
    column, the timestamp column, or a column ``feature.transform`` tries to
    read -- before any row is committed to the feature table, so a rejected
    materialisation never leaves the store partially updated.
    """
    computed: list[FeatureRow] = []
    for index, row in enumerate(source_table):
        entity_value = _required(feature, row, feature.entity, index, role="entity key")
        event_timestamp = _required(feature, row, feature.timestamp_column, index, role="timestamp")
        value = _run_transform(feature, row, index)
        computed.append(
            {
                "entity": entity_value,
                "event_timestamp": event_timestamp,
                "value": value,
            }
        )
    return computed


def _required(feature: Feature, row: Row, column: str, index: int, *, role: str) -> Any:
    if column not in row:
        raise UnknownColumnError(
            f"feature '{feature.name}' needs its {role} column '{column}' from "
            f"source table '{feature.source}', but row {index} does not have it"
        )
    return row[column]


def _run_transform(feature: Feature, row: Row, index: int) -> Any:
    try:
        return feature.transform(row)
    except KeyError as exc:
        missing = exc.args[0] if exc.args else str(exc)
        raise UnknownColumnError(
            f"feature '{feature.name}' transform reads column '{missing}', which is "
            f"absent from source table '{feature.source}' (row {index})"
        ) from exc
