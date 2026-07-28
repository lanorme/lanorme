"""Feature declarations.

A :class:`Feature` is a declaration, not data: it says how to compute one
named value per entity from a source table. Nothing here touches any actual
table; that happens in :mod:`featurestore.materialize`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: One row of a source (or feature) table.
Row = dict[str, Any]

#: A feature transform: takes one source-table row, returns the feature value.
Transform = Callable[[Row], Any]


@dataclass(frozen=True)
class Feature:
    """The declaration of a single feature.

    Parameters
    ----------
    name:
        The feature's unique name (the public handle used everywhere else:
        registration, materialisation, queries).
    entity:
        The name of the column, in the source table, that identifies the
        entity the feature describes (for example ``"user_id"``).
    dtype:
        The Python type the computed value is expected to have (for example
        ``float``, ``int``, or ``str``). Recorded for documentation and
        shown by the registry; the store does not coerce or reject on it.
    source:
        The name of the source table this feature is computed from (a
        logical name, not a table itself -- used to look up the right table
        when materialising several features at once).
    transform:
        A callable that takes one source-table row (a ``dict``) and returns
        the feature's value for that row. It should read the row only by
        the columns it actually needs; reading a column the source table
        does not have is what gets rejected at materialisation time.
    owner:
        Free-text identifier of the person or team responsible for the
        feature. Shown by the registry.
    timestamp_column:
        The name of the column, in the source table, holding the event
        timestamp at which each row became known. Used to make point-in-time
        joins correct. Defaults to ``"event_timestamp"``.
    description:
        Optional human-readable description of the feature.
    depends_on:
        Names of other registered features this one derives from, in
        addition to its ``source`` table (for example a feature computed on
        top of another feature's output, conceptually, even though nothing
        here wires their values together automatically). Purely a lineage
        declaration -- see :mod:`featurestore.lineage` -- checked at
        registration time for a cycle and for referencing a feature that is
        not registered.
    """

    name: str
    entity: str
    dtype: type
    source: str
    transform: Transform
    owner: str = ""
    timestamp_column: str = "event_timestamp"
    description: str = ""
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("a feature must have a non-empty string name")
        if not isinstance(self.entity, str) or not self.entity:
            raise ValueError("a feature must declare a non-empty entity key column")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("a feature must declare a non-empty source table name")
        if not isinstance(self.timestamp_column, str) or not self.timestamp_column:
            raise ValueError("a feature must declare a non-empty timestamp column")
        if not isinstance(self.dtype, type):
            raise ValueError("dtype must be a Python type, e.g. float, int, or str")
        if not callable(self.transform):
            raise ValueError("transform must be callable")
        if not isinstance(self.depends_on, tuple) or not all(
            isinstance(dep, str) and dep for dep in self.depends_on
        ):
            raise ValueError("depends_on must be a tuple of non-empty strings")
        if self.name in self.depends_on:
            raise ValueError(f"feature '{self.name}' cannot depend on itself")
