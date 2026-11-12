"""A Bunch-style search for the partition of an import graph with the best Modularization Quality.

Bunch treats clustering as search: a partition of the dependency graph is
scored by a fitness function, Modularization Quality (MQ), and a hill climb
moves one node at a time while the score improves [@mitchell2006bunch]. The
score used here is the cluster-factor form of MQ, the one the Bunch tool
calls TurboMQ: the sum over clusters of a cluster factor,
``2 * intra / (2 * intra + inter)``, where ``intra`` counts the edges inside
the cluster and ``inter`` the edges with exactly one end in it; a cluster
with no internal edge scores 0. It rewards cohesion and penalises
coupling in one number, which is the single-objective formulation later work
splits into several objectives [@praditwong2011clustering].

The climb starts from a random partition and visits the nodes in random
order. A node moves to the neighbouring cluster, or to a cluster of its own,
that raises MQ the most; a cluster the node has no edge to can never beat a
cluster of its own, so those are not tried. The climb stops when a full pass
moves nothing. It is repeated from several random starts and every local
optimum is kept, since how much the optima disagree is itself a result.

Two partitions are compared with the adjusted Rand index
[@hubert1985comparing]: 1 for identical partitions, about 0 for independent
ones.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field


def _compute_factor(*, intra: int, inter: int) -> float:
    return 2 * intra / (2 * intra + inter) if intra else 0.0


@dataclass
class Partition:
    """An assignment of nodes to clusters, with each cluster's edge counts kept current."""

    neighbours: dict[str, Counter[str]]
    cluster_of: dict[str, int]
    intra: Counter[int] = field(default_factory=Counter)
    inter: Counter[int] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        for node, linked in self.neighbours.items():
            for other, weight in linked.items():
                if self.cluster_of[node] == self.cluster_of[other]:
                    self.intra[self.cluster_of[node]] += weight
                else:
                    self.inter[self.cluster_of[node]] += 2 * weight
        for counts in (self.intra, self.inter):
            for cluster in counts:
                counts[cluster] //= 2

    def compute_quality(self) -> float:
        """The partition's Modularization Quality."""
        clusters = set(self.cluster_of.values())
        return sum(
            _compute_factor(intra=self.intra[cluster], inter=self.inter[cluster])
            for cluster in clusters
        )

    def _count_links(self, node: str) -> Counter[int]:
        """How many edges join *node* to each cluster."""
        links: Counter[int] = Counter()
        for other, weight in self.neighbours[node].items():
            links[self.cluster_of[other]] += weight
        return links

    def _measure_gain(self, *, node: str, target: int, links: Counter[int]) -> float:
        """The change in MQ if *node* moved to cluster *target*."""
        source = self.cluster_of[node]
        degree = sum(links.values())
        before = _compute_factor(intra=self.intra[source], inter=self.inter[source])
        before += _compute_factor(intra=self.intra[target], inter=self.inter[target])
        after = _compute_factor(
            intra=self.intra[source] - links[source],
            inter=self.inter[source] + 2 * links[source] - degree,
        )
        after += _compute_factor(
            intra=self.intra[target] + links[target],
            inter=self.inter[target] + degree - 2 * links[target],
        )
        return after - before

    def move(self, *, node: str, target: int) -> None:
        """Move *node* to cluster *target* and update both clusters' edge counts."""
        links = self._count_links(node)
        source = self.cluster_of[node]
        degree = sum(links.values())
        self.intra[source] -= links[source]
        self.inter[source] += 2 * links[source] - degree
        self.intra[target] += links[target]
        self.inter[target] += degree - 2 * links[target]
        self.cluster_of[node] = target

    def find_best_move(self, node: str, *, spare: int) -> tuple[float, int]:
        """The largest MQ gain from moving *node*, and the cluster that gives it."""
        links = self._count_links(node)
        source = self.cluster_of[node]
        targets = [cluster for cluster in links if cluster != source]
        targets.append(spare)
        gains = [
            (self._measure_gain(node=node, target=target, links=links), target)
            for target in targets
        ]
        return max(gains)


def build_neighbours(*, nodes: list[str], edges: set[tuple[str, str]]) -> dict[str, Counter[str]]:
    """The undirected weighted adjacency of a directed graph: a mutual import weighs 2."""
    neighbours: dict[str, Counter[str]] = {node: Counter() for node in nodes}
    for source, target in edges:
        if source != target and source in neighbours and target in neighbours:
            neighbours[source][target] += 1
            neighbours[target][source] += 1
    return neighbours


def measure_quality(*, neighbours: dict[str, Counter[str]], cluster_of: dict[str, int]) -> float:
    """The Modularization Quality of a given partition."""
    return Partition(neighbours=neighbours, cluster_of=dict(cluster_of)).compute_quality()


def find_local_optimum(
    *,
    neighbours: dict[str, Counter[str]],
    generator: random.Random,
) -> dict[str, int]:
    """One hill climb from a random partition to a local optimum of MQ."""
    nodes = sorted(neighbours)
    start = {node: generator.randrange(max(1, len(nodes) // 2)) for node in nodes}
    partition = Partition(neighbours=neighbours, cluster_of=start)
    spare = len(nodes)
    improved = True
    while improved:
        improved = False
        generator.shuffle(nodes)
        for node in nodes:
            gain, target = partition.find_best_move(node, spare=spare)
            if gain > 1e-12:
                partition.move(node=node, target=target)
                spare += target == spare
                improved = True
    return partition.cluster_of


def search(
    *,
    neighbours: dict[str, Counter[str]],
    restarts: int,
    seed: int,
) -> list[dict[str, int]]:
    """The local optima of *restarts* climbs, best Modularization Quality first."""
    generator = random.Random(seed)
    found = [
        find_local_optimum(neighbours=neighbours, generator=generator) for _index in range(restarts)
    ]
    return sorted(
        found,
        key=lambda cluster_of: -measure_quality(neighbours=neighbours, cluster_of=cluster_of),
    )


def _count_pairs(count: int) -> int:
    return count * (count - 1) // 2


def compute_adjusted_rand(*, first: dict[str, int], second: dict[str, int]) -> float:
    """The adjusted Rand index of two partitions of the same nodes."""
    cells = Counter((first[node], second[node]) for node in first)
    rows = Counter(first.values())
    columns = Counter(second.values())
    together = sum(_count_pairs(count) for count in cells.values())
    in_first = sum(_count_pairs(count) for count in rows.values())
    in_second = sum(_count_pairs(count) for count in columns.values())
    expected = in_first * in_second / _count_pairs(len(first)) if len(first) > 1 else 0.0
    largest = (in_first + in_second) / 2
    return (together - expected) / (largest - expected) if largest != expected else 1.0
