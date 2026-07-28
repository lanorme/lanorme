"""Exceptions raised by the feature store.

Every error the store deliberately rejects on (as opposed to bugs) is a
subclass of :class:`FeatureStoreError`, so callers can catch the base class
if they just want to know "did the store refuse to do this".
"""

from __future__ import annotations


class FeatureStoreError(Exception):
    """Base class for every error the feature store raises on purpose."""


class DuplicateFeatureError(FeatureStoreError):
    """Raised when registering a feature name that is already registered."""


class UnknownFeatureError(FeatureStoreError):
    """Raised when a feature name is referenced but was never registered."""


class UnknownColumnError(FeatureStoreError):
    """Raised when a row is missing a column something needed to read.

    Covers both directions: a feature's transform (or its declared entity /
    timestamp column) reading a column absent from the source table during
    materialisation, and a point-in-time join reading an entity or
    timestamp column absent from the entity rows it was given.
    """


class MissingTimestampError(FeatureStoreError):
    """Raised when a point-in-time query or join is missing an event timestamp."""


class UnknownVersionError(FeatureStoreError):
    """Raised when a feature version number is referenced but does not exist."""


class NonMonotonicVersionError(FeatureStoreError):
    """Raised when a new feature version's ``effective_from`` would precede the
    ``effective_from`` of the version that is currently latest.

    Versions are looked up, for a point-in-time query, by finding the one
    with the greatest ``effective_from`` at or before the query's ``as_of``.
    That lookup only makes sense if version numbers and ``effective_from``
    increase together, so a new version is never allowed to become
    "current" earlier than the version it follows.
    """


class InvalidRangeError(FeatureStoreError):
    """Raised when a backfill range is empty, backwards, or has a chunk size
    that does not advance it forward.
    """


class LineageCycleError(FeatureStoreError):
    """Raised when registering a feature, or adding a new version of one,
    would create a cycle in the lineage graph: feature A depending
    (directly or transitively, through ``depends_on``) on a feature that
    itself depends on A.
    """
