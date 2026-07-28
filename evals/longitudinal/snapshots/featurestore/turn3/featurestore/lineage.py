"""Feature lineage: which source table and which upstream feature versions
each feature version derives from, and the transitive closure of that graph.

A :class:`~featurestore.feature.Feature` names its direct dependencies in two
places: ``source`` (one source table, required) and ``depends_on`` (zero or
more upstream feature names, optional). Nothing here changes how a feature's
value is computed -- ``depends_on`` is a provenance declaration only, not a
data pipeline -- but it lets a caller ask "what does this feature ultimately
rest on" and lets the store refuse a declaration that would make that
question unanswerable (a cycle).

Every function here reads the registry as it stands right now: an upstream
name in ``depends_on`` always resolves to that feature's *latest* registered
version. That keeps the graph simple to reason about (one edge per name, not
one per version pair) at the cost of not pinning historical upstream
versions here -- a training-set snapshot (:mod:`featurestore.snapshot`)
pins the exact versions it used itself, separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from featurestore.errors import LineageCycleError, UnknownFeatureError
from featurestore.feature import Feature
from featurestore.versioning import FeatureVersion

if TYPE_CHECKING:
    from featurestore.registry import Registry


@dataclass(frozen=True)
class Lineage:
    """The direct (one-hop) lineage of one feature version."""

    feature_name: str
    version: int
    source: str
    upstream: tuple[FeatureVersion, ...]


@dataclass(frozen=True)
class LineageClosure:
    """The transitive closure of one feature version's lineage: every source
    table and every upstream feature version reachable by following
    ``depends_on`` edges, however many hops away. Does not include the
    feature version itself.
    """

    feature_name: str
    version: int
    source_tables: frozenset[str]
    feature_versions: frozenset[FeatureVersion]


def direct_lineage(feature_version: FeatureVersion, registry: Registry) -> Lineage:
    """The source table and (latest-version) upstream features that
    ``feature_version`` declares itself as depending on.
    """
    feature = feature_version.feature
    upstream = tuple(registry.latest_version(name) for name in feature.depends_on)
    return Lineage(
        feature_name=feature.name,
        version=feature_version.version,
        source=feature.source,
        upstream=upstream,
    )


def transitive_closure(feature_version: FeatureVersion, registry: Registry) -> LineageClosure:
    """Every source table and upstream feature version ``feature_version``
    rests on, transitively.
    """
    source_tables: set[str] = set()
    feature_versions: set[FeatureVersion] = set()
    _walk(feature_version, registry, source_tables, feature_versions, frozenset())
    return LineageClosure(
        feature_name=feature_version.feature.name,
        version=feature_version.version,
        source_tables=frozenset(source_tables),
        feature_versions=frozenset(feature_versions),
    )


def _walk(
    feature_version: FeatureVersion,
    registry: Registry,
    source_tables: set[str],
    feature_versions: set[FeatureVersion],
    visiting: frozenset[str],
) -> None:
    name = feature_version.feature.name
    if name in visiting:
        # Registration-time checks (see assert_acyclic) should make this
        # unreachable; stay defensive rather than recursing forever.
        raise LineageCycleError(
            f"lineage cycle detected while walking from '{name}': {' -> '.join([*visiting, name])}"
        )
    visiting = visiting | {name}
    direct = direct_lineage(feature_version, registry)
    source_tables.add(direct.source)
    for upstream_version in direct.upstream:
        feature_versions.add(upstream_version)
        _walk(upstream_version, registry, source_tables, feature_versions, visiting)


def assert_acyclic(feature: Feature, registry: Registry) -> None:
    """Raise if declaring ``feature`` -- about to be registered, or added as
    a new version under its own name -- would create a cycle in the
    lineage graph, given the features already registered.

    Raises :class:`~featurestore.errors.UnknownFeatureError` if any name in
    ``feature.depends_on`` is not itself a registered feature: a feature
    cannot derive from one that does not exist yet. Raises
    :class:`~featurestore.errors.LineageCycleError` if following
    ``depends_on`` edges from any of those names leads back to
    ``feature.name``.
    """
    for name in feature.depends_on:
        if name not in registry:
            raise UnknownFeatureError(f"feature '{feature.name}' depends on '{name}', which is not registered")
    path = _find_path_to(feature.name, feature.depends_on, registry)
    if path is not None:
        rendered = " -> ".join([feature.name, *path])
        raise LineageCycleError(
            f"feature '{feature.name}' cannot depend on {list(feature.depends_on)}: "
            f"that would create a lineage cycle: {rendered}"
        )


def _find_path_to(target: str, names: tuple[str, ...], registry: Registry) -> list[str] | None:
    """Does following ``depends_on`` edges from any name in ``names`` reach
    ``target``? Returns the path from the first hop to ``target`` if so,
    else ``None``.
    """
    visited: set[str] = set()

    def dfs(name: str) -> list[str] | None:
        if name == target:
            return [name]
        if name in visited:
            return None
        visited.add(name)
        for upstream in registry.latest_version(name).feature.depends_on:
            found = dfs(upstream)
            if found is not None:
                return [name, *found]
        return None

    for name in names:
        found = dfs(name)
        if found is not None:
            return found
    return None
