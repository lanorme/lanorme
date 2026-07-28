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
