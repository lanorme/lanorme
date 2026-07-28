"""Feature versioning.

A feature's declaration is versioned: changing a transform (or any other
part of a :class:`~featurestore.feature.Feature`) does not mutate the
existing declaration, it adds a new :class:`FeatureVersion` under the same
name. Both the old and the new version keep their own materialised feature
table (see :mod:`featurestore.registry`), so a point-in-time query for a
moment before the new version existed still sees the old one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from featurestore.feature import Feature


class _Genesis:
    """Sentinel ``effective_from`` that sorts earlier than any real
    timestamp, whatever type the caller uses for timestamps (``datetime``,
    an integer epoch, or anything else orderable).

    A feature's very first version defaults to this. That makes a
    single-version feature behave exactly as if versioning did not exist:
    every ``as_of``, however far in the past, resolves to it, matching the
    pre-versioning behaviour of the store.
    """

    def __lt__(self, other: object) -> bool:
        return not isinstance(other, _Genesis)

    def __le__(self, other: object) -> bool:
        return True

    def __gt__(self, other: object) -> bool:
        return False

    def __ge__(self, other: object) -> bool:
        return isinstance(other, _Genesis)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Genesis)

    def __hash__(self) -> int:
        return hash("featurestore.versioning._Genesis")

    def __repr__(self) -> str:
        return "GENESIS"


#: The default ``effective_from`` of a feature's first version: sorts
#: before any real timestamp, so it covers all of history unless a later
#: version supersedes it.
GENESIS: Any = _Genesis()


@dataclass(frozen=True)
class FeatureVersion:
    """One version of a feature's declaration.

    ``version`` numbers start at 1 and increase by one each time
    :meth:`featurestore.store.FeatureStore.add_version` is called for the
    feature's name. ``effective_from`` is the event timestamp at which this
    version became the current one for point-in-time queries: a query
    ``as_of`` a moment before a version's ``effective_from`` never sees it,
    even when it is the latest version that exists.
    """

    feature: Feature
    version: int
    effective_from: Any

    def __post_init__(self) -> None:
        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError("a feature version number must be a positive integer")
