"""How well one signal separates two groups of packages, with the uncertainty of a small sample.

The measure is the area under the receiver operating characteristic curve
(AUC): the probability that a package drawn from the first group scores higher
than one drawn from the second, ties counting half [@hanley1982meaning]. An AUC
of 0.5 is no separation; 1.0 means every first-group package scores above every
second-group one, 0.0 the reverse.

The packages are not independent: one repository contributes several, and one
A/B run contributes the same package at three stages. The interval is
therefore a cluster bootstrap [@efron1993bootstrap]: lineages are resampled
with replacement, never single packages, and a resample holding only one group
is redrawn.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass

RESAMPLES = 2000
_SEED = 20261112
_MAX_REDRAWS = 50


def compute_auc(*, first: list[float], second: list[float]) -> float | None:
    """The probability that a *first* value exceeds a *second* one, ties counting half."""
    if not first or not second:
        return None
    wins = sum(
        1.0 if left > right else 0.5 if left == right else 0.0 for left in first for right in second
    )
    return wins / (len(first) * len(second))


@dataclass(frozen=True)
class Observation:
    """One package's value of a signal, its group and the lineage it resamples with."""

    value: float
    is_first: bool
    lineage: str


def _split_groups(observations: list[Observation]) -> tuple[list[float], list[float]]:
    first = [found.value for found in observations if found.is_first]
    second = [found.value for found in observations if not found.is_first]
    return first, second


def _resample_auc(*, by_lineage: dict[str, list[Observation]], generator: random.Random) -> float:
    """The AUC of one cluster resample, redrawn until both groups are present."""
    lineages = sorted(by_lineage)
    for _attempt in range(_MAX_REDRAWS):
        drawn = [
            found
            for lineage in generator.choices(lineages, k=len(lineages))
            for found in by_lineage[lineage]
        ]
        first, second = _split_groups(drawn)
        score = compute_auc(first=first, second=second)
        if score is not None:
            return score
    return 0.5


def compute_interval(observations: list[Observation]) -> tuple[float, float] | None:
    """The 95% cluster-bootstrap interval of the AUC, or ``None`` with fewer than two lineages."""
    by_lineage: dict[str, list[Observation]] = {}
    for found in observations:
        by_lineage.setdefault(found.lineage, []).append(found)
    if len(by_lineage) < 2:
        return None
    generator = random.Random(_SEED)
    scores = sorted(
        _resample_auc(by_lineage=by_lineage, generator=generator) for _index in range(RESAMPLES)
    )
    return scores[int(0.025 * RESAMPLES)], scores[int(0.975 * RESAMPLES) - 1]


def summarise_separation(observations: list[Observation]) -> dict[str, object]:
    """The AUC of one signal, its interval, and each group's size and median."""
    first, second = _split_groups(observations)
    return {
        "auc": compute_auc(first=first, second=second),
        "interval": compute_interval(observations),
        "first_count": len(first),
        "second_count": len(second),
        "first_median": statistics.median(first) if first else None,
        "second_median": statistics.median(second) if second else None,
        "lineages": len({found.lineage for found in observations}),
    }
