"""Compare an audit's holdout numbers with a previous audit and list regressions.

The holdout split is the only one a change may not tune against, so it is the
only one the gate trusts: a rule regresses when its holdout precision or recall
falls below the previous result minus a tolerance. Dev numbers are reported
but never gate, since tuning is allowed to move them.

A rule the baseline has no holdout block for (an audit written before the
split, or a corpus too small to split) is skipped and named, not failed. A
rule the baseline scored that this run did not score, or that errored, is a
regression: dropping a scorer must not silently drop its gate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from labelled_corpus import ScoreRecord

DEFAULT_TOLERANCE = 0.02
GATED_METRICS = ("precision", "recall")
# A drop of exactly the tolerance passes; the slack absorbs float rounding.
_ROUNDING_SLACK = 1e-9


class GateOutcome(TypedDict):
    """The gate's verdict: the baseline it read, what regressed, what it skipped."""

    baseline: str
    tolerance: float
    regressions: list[str]
    skipped: list[str]


def find_latest_result(*, results_dir: Path) -> Path | None:
    """Return the ``v<X.Y.Z>.json`` with the highest version, or None if there is none."""
    released = [
        path
        for path in results_dir.glob("v*.json")
        if all(part.isdigit() for part in path.stem[1:].split("."))
    ]
    return max(
        released,
        key=lambda path: tuple(int(part) for part in path.stem[1:].split(".")),
        default=None,
    )


def resolve_baseline(*, gate: str | None, results_dir: Path) -> Path | None:
    """Return the baseline audit to gate against, or None when no gate was asked for.

    ``latest`` names the newest ``v*.json`` under *results_dir*. Raises
    FileNotFoundError when the named baseline does not exist.
    """
    if not gate:
        return None
    baseline = find_latest_result(results_dir=results_dir) if gate == "latest" else Path(gate)
    if baseline is None or not baseline.is_file():
        raise FileNotFoundError(f"--gate baseline {gate} is not a file")
    return baseline


def read_baseline(*, path: Path) -> list[ScoreRecord]:
    """Load the ``accuracy`` list of a previous audit JSON."""
    report = json.loads(path.read_text(encoding="utf-8"))
    return list(report.get("accuracy", []))


def index_by_rule(*, records: list[ScoreRecord]) -> dict[str, ScoreRecord]:
    """Map each rule code to its accuracy record."""
    return {record["rule"]: record for record in records if "rule" in record}


def compare_rule(
    *,
    rule: str,
    previous: ScoreRecord,
    current: ScoreRecord | None,
    tolerance: float,
) -> list[str]:
    """Return the regressions of one rule's holdout metrics, if any."""
    if current is None or "error" in current:
        return [f"{rule}: scored in the baseline but not in this run"]
    before, now = previous.get("holdout"), current.get("holdout")
    if now is None:
        return [f"{rule}: the baseline has a holdout split but this run has none"]
    found: list[str] = []
    for metric in GATED_METRICS:
        old, new = before.get(metric), now.get(metric)
        if old is not None and new is not None and new < old - tolerance - _ROUNDING_SLACK:
            found.append(
                f"{rule}: holdout {metric} {new:.3f} is below the baseline "
                f"{old:.3f} minus {tolerance:.2f}",
            )
    return found


def find_regressions(
    *,
    baseline: list[ScoreRecord],
    current: list[ScoreRecord],
    tolerance: float = DEFAULT_TOLERANCE,
    baseline_name: str = "",
) -> GateOutcome:
    """Compare *current* accuracy records against *baseline* ones."""
    now = index_by_rule(records=current)
    regressions: list[str] = []
    skipped: list[str] = []
    for rule, previous in sorted(index_by_rule(records=baseline).items()):
        if "error" in previous or previous.get("holdout") is None:
            skipped.append(f"{rule}: no holdout numbers in the baseline")
            continue
        regressions.extend(
            compare_rule(rule=rule, previous=previous, current=now.get(rule), tolerance=tolerance),
        )
    return {
        "baseline": baseline_name,
        "tolerance": tolerance,
        "regressions": regressions,
        "skipped": skipped,
    }


def format_gate(*, outcome: GateOutcome) -> list[str]:
    """Render the gate's regressions and a one-line tally for the audit summary."""
    lines = [f"gate: REGRESSION {regression}" for regression in outcome["regressions"]]
    lines.append(
        f"gate: {len(outcome['regressions'])} regression(s), {len(outcome['skipped'])} rule(s) "
        f"without a holdout baseline, against {outcome['baseline']}",
    )
    return lines
