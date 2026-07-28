"""The feature registry: what features exist, who owns them, which versions
they have, how fresh each version is, and which ranges have been backfilled.

The registry is the source of truth for declared features, their versions,
and the feature table each version has been materialised into. It does not
know how to compute a feature (that is :mod:`featurestore.materialize`'s
job) or how to run a point-in-time query (that is
:class:`featurestore.store.FeatureStore`'s job) -- it only stores and
reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from featurestore.backfill import Range, merge_ranges
from featurestore.errors import (
    DuplicateFeatureError,
    NonMonotonicVersionError,
    UnknownFeatureError,
    UnknownVersionError,
)
from featurestore.feature import Feature
from featurestore.lineage import Lineage, LineageClosure, assert_acyclic, direct_lineage, transitive_closure
from featurestore.materialize import FeatureRow
from featurestore.versioning import GENESIS, FeatureVersion


@dataclass
class _VersionEntry:
    feature_version: FeatureVersion
    materialized_rows: list[FeatureRow] = field(default_factory=list)
    last_materialized_at: datetime | None = None
    completed_ranges: list[Range] = field(default_factory=list)


@dataclass
class _Entry:
    versions: dict[int, _VersionEntry] = field(default_factory=dict)


@dataclass(frozen=True)
class FreshnessReport:
    """A snapshot of how fresh one feature version's materialised data is.

    ``last_materialized_at`` is wall-clock time: when ``materialize`` was
    last run for this feature version. ``latest_event_timestamp`` is data
    time: the most recent event timestamp among the materialised rows,
    i.e. how recent the underlying facts are, not how recently the job ran.
    """

    name: str
    version: int
    owner: str
    row_count: int
    is_materialized: bool
    last_materialized_at: datetime | None
    latest_event_timestamp: Any


@dataclass(frozen=True)
class BackfillReport:
    """Which ranges of one feature version have been backfilled."""

    feature_name: str
    version: int
    completed_ranges: list[Range]


class Registry:
    """Tracks every declared feature, its versions, and their materialised data."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    # -- declaration and versioning ---------------------------------------

    def register(self, feature: Feature, *, effective_from: Any = None) -> FeatureVersion:
        """Declare a brand new feature at version 1. Rejects a name that is
        already registered -- use :meth:`add_version` to evolve an existing
        feature instead of mutating it.

        ``effective_from`` defaults to :data:`featurestore.versioning.GENESIS`,
        so an as-yet single-version feature answers every point-in-time
        query regardless of ``as_of``, exactly as if versioning did not
        exist.
        """
        if feature.name in self._entries:
            raise DuplicateFeatureError(f"a feature named '{feature.name}' is already registered")
        assert_acyclic(feature, self)
        version = FeatureVersion(
            feature=feature,
            version=1,
            effective_from=effective_from if effective_from is not None else GENESIS,
        )
        self._entries[feature.name] = _Entry(versions={1: _VersionEntry(feature_version=version)})
        return version

    def add_version(self, feature: Feature, *, effective_from: Any = None) -> FeatureVersion:
        """Add a new version of an already-registered feature (matched by
        ``feature.name``), rather than mutating the version that exists.

        The prior version, and its materialised data, are left untouched
        and stay queryable. ``effective_from`` defaults to the current wall
        clock time, and must not precede the latest existing version's
        ``effective_from`` (raises :class:`NonMonotonicVersionError`),
        since point-in-time queries pick a version by finding the greatest
        ``effective_from`` at or before ``as_of``.
        """
        entry = self._entry(feature.name)
        assert_acyclic(feature, self)
        latest = self._latest_entry(entry)
        new_number = latest.feature_version.version + 1
        resolved = effective_from if effective_from is not None else datetime.now(timezone.utc)
        if resolved < latest.feature_version.effective_from:
            raise NonMonotonicVersionError(
                f"version {new_number} of feature '{feature.name}' would take effect at "
                f"{resolved!r}, before version {latest.feature_version.version}'s "
                f"effective_from of {latest.feature_version.effective_from!r}"
            )
        version = FeatureVersion(feature=feature, version=new_number, effective_from=resolved)
        entry.versions[new_number] = _VersionEntry(feature_version=version)
        return version

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def get(self, name: str) -> Feature:
        """Look up a registered feature's current (latest version's) declaration."""
        return self.latest_version(name).feature

    def get_version(self, name: str, version: int) -> FeatureVersion:
        """Look up one specific version of a registered feature."""
        return self._version_entry(name, version).feature_version

    def latest_version(self, name: str) -> FeatureVersion:
        """The highest-numbered version registered for ``name``."""
        entry = self._entry(name)
        return self._latest_entry(entry).feature_version

    def version_at(self, name: str, as_of: Any) -> FeatureVersion | None:
        """The version of ``name`` that was current at ``as_of``: the one
        with the greatest ``effective_from`` at or before ``as_of``.

        Returns ``None`` if ``as_of`` precedes every version's
        ``effective_from`` -- no version of this feature existed yet.
        """
        entry = self._entry(name)
        candidates = [
            version_entry.feature_version
            for version_entry in entry.versions.values()
            if version_entry.feature_version.effective_from <= as_of
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda fv: (fv.effective_from, fv.version))

    def list_versions(self, name: str) -> list[FeatureVersion]:
        """Every version of ``name``, oldest first."""
        entry = self._entry(name)
        return [
            version_entry.feature_version
            for version_entry in sorted(entry.versions.values(), key=lambda ve: ve.feature_version.version)
        ]

    def list_features(self) -> list[Feature]:
        """The current (latest version's) declaration of every registered
        feature, sorted by name.
        """
        return [self.latest_version(name).feature for name in sorted(self._entries)]

    # -- materialised data --------------------------------------------------

    def set_materialized_rows(self, name: str, version: int, rows: list[FeatureRow]) -> None:
        version_entry = self._version_entry(name, version)
        version_entry.materialized_rows = rows
        version_entry.last_materialized_at = datetime.now(timezone.utc)

    def materialized_rows(self, name: str, version: int) -> list[FeatureRow]:
        return self._version_entry(name, version).materialized_rows

    # -- backfill progress --------------------------------------------------

    def completed_ranges(self, name: str, version: int) -> list[Range]:
        """The ranges of ``name``'s ``version`` that have been fully backfilled."""
        return list(self._version_entry(name, version).completed_ranges)

    def mark_range_completed(self, name: str, version: int, start: Any, end: Any) -> None:
        """Record ``[start, end)`` as backfilled, coalescing it with any
        existing completed ranges it overlaps or touches.
        """
        version_entry = self._version_entry(name, version)
        version_entry.completed_ranges = merge_ranges([*version_entry.completed_ranges, (start, end)])

    def backfill_report(self, feature_name: str | None = None) -> list[BackfillReport]:
        """Which versions have been backfilled, and over which ranges.

        Restricts to ``feature_name`` if given (raises
        :class:`~featurestore.errors.UnknownFeatureError` if it is not
        registered); otherwise covers every registered feature. Omits a
        version with no completed backfill ranges at all -- a feature only
        ever ``materialize``d, never ``backfill``ed, does not show up here.
        """
        names = [feature_name] if feature_name is not None else sorted(self._entries)
        reports = []
        for name in names:
            for feature_version in self.list_versions(name):
                ranges = self.completed_ranges(name, feature_version.version)
                if ranges:
                    reports.append(
                        BackfillReport(feature_name=name, version=feature_version.version, completed_ranges=ranges)
                    )
        return reports

    # -- freshness ------------------------------------------------------------

    def freshness(self, name: str, version: int | None = None) -> FreshnessReport:
        """Owner, row count, and freshness for one feature version, defaulting
        to its latest version.
        """
        feature_version = self.get_version(name, version) if version is not None else self.latest_version(name)
        version_entry = self._version_entry(name, feature_version.version)
        rows = version_entry.materialized_rows
        latest_event_timestamp = max((r["event_timestamp"] for r in rows), default=None)
        return FreshnessReport(
            name=name,
            version=feature_version.version,
            owner=feature_version.feature.owner,
            row_count=len(rows),
            is_materialized=version_entry.last_materialized_at is not None,
            last_materialized_at=version_entry.last_materialized_at,
            latest_event_timestamp=latest_event_timestamp,
        )

    def freshness_report(self) -> list[FreshnessReport]:
        """Owner, row count, and freshness for every registered feature's
        latest version.
        """
        return [self.freshness(name) for name in sorted(self._entries)]

    # -- lineage --------------------------------------------------------------

    def lineage(self, name: str, version: int | None = None) -> Lineage:
        """The direct lineage of one feature version, defaulting to its
        latest version: its source table and the (latest-version) upstream
        features named in its ``depends_on``.
        """
        feature_version = self.get_version(name, version) if version is not None else self.latest_version(name)
        return direct_lineage(feature_version, self)

    def lineage_closure(self, name: str, version: int | None = None) -> LineageClosure:
        """The transitive closure of one feature version's lineage,
        defaulting to its latest version: every source table and upstream
        feature version reachable, however many hops away.
        """
        feature_version = self.get_version(name, version) if version is not None else self.latest_version(name)
        return transitive_closure(feature_version, self)

    # -- internals ----------------------------------------------------------

    def _entry(self, name: str) -> _Entry:
        try:
            return self._entries[name]
        except KeyError:
            raise UnknownFeatureError(f"no feature named '{name}' is registered") from None

    def _version_entry(self, name: str, version: int) -> _VersionEntry:
        entry = self._entry(name)
        try:
            return entry.versions[version]
        except KeyError:
            raise UnknownVersionError(f"feature '{name}' has no version {version}") from None

    @staticmethod
    def _latest_entry(entry: _Entry) -> _VersionEntry:
        return max(entry.versions.values(), key=lambda ve: ve.feature_version.version)
