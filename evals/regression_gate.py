"""Hold an audit's holdout numbers and files against the recorded audit history.

The holdout split is the only one a change may not tune against, so it is the
only one the gate trusts. The gate fails on two things:

- a holdout edit: a holdout file that a baseline audit recorded has gone, or
  its digest (its content and its labels) has changed, unless
  ``evals/holdout_revisions.json`` accepts that exact change;
- a holdout regression: a rule's holdout precision or recall falls below the
  best value any recorded audit of the same holdout files reached, minus a
  tolerance. Gating against the best, not the latest, stops small drops that
  each pass the tolerance from adding up release after release.

Dev numbers are reported but never gate, since tuning is allowed to move them.
A rule with no comparable baseline (no audit recorded holdout numbers for it,
or its holdout files changed since every audit that did) is skipped and named,
and a run that gates no rule at all says so and why. A rule a comparable
baseline scored that this run did not score, or that errored, is a regression:
dropping a scorer must not silently drop its gate.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypedDict

from labelled_corpus import (
    FileEntry,
    Metrics,
    ScoreRecord,
    find_corpus_files,
    read_labels,
)

DEFAULT_TOLERANCE = 0.02
GATED_METRICS = ("precision", "recall")
# A drop of exactly the tolerance passes; the slack absorbs float rounding.
_ROUNDING_SLACK = 1e-9

# Corpus name to {path inside the corpus: digest} for every holdout file.
HoldoutDigests = dict[str, dict[str, str]]


class AuditRecord(TypedDict, total=False):
    """The parts of a recorded audit JSON the gate reads."""

    accuracy: list[ScoreRecord]
    holdout_digests: HoldoutDigests


class HoldoutRevision(TypedDict):
    """An accepted holdout edit: the file's new digest (None when removed) and why."""

    digest: str | None
    reason: str


class GateOutcome(TypedDict):
    """The gate's verdict: what it compared against, what failed, what it skipped."""

    baseline: str
    tolerance: float
    gated: list[str]
    regressions: list[str]
    holdout_changes: list[str]
    skipped: list[str]
    notes: list[str]


def find_released_results(*, results_dir: Path) -> list[Path]:
    """Return every ``v<X.Y.Z>.json`` under *results_dir*, oldest version first."""
    released = [
        path
        for path in results_dir.glob("v*.json")
        if all(part.isdigit() for part in path.stem[1:].split("."))
    ]
    return sorted(released, key=lambda path: tuple(int(part) for part in path.stem[1:].split(".")))


def find_latest_result(*, results_dir: Path) -> Path | None:
    """Return the ``v<X.Y.Z>.json`` with the highest version, or None if there is none."""
    released = find_released_results(results_dir=results_dir)
    return released[-1] if released else None


def resolve_history(*, gate: str | None, results_dir: Path) -> list[Path]:
    """Return the audits to gate against, oldest first; empty when no gate was asked for.

    ``latest`` names every released ``v*.json`` under *results_dir*, so the gate
    holds the best number any release reached; a path names that one audit.
    Raises FileNotFoundError when the named baseline does not exist.
    """
    if not gate:
        return []
    history = find_released_results(results_dir=results_dir) if gate == "latest" else [Path(gate)]
    if not history or not all(path.is_file() for path in history):
        raise FileNotFoundError(f"--gate baseline {gate} is not a file")
    return history


def read_audit(*, path: Path) -> AuditRecord:
    """Load the accuracy list and holdout digests of a recorded audit JSON."""
    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        "accuracy": list(report.get("accuracy", [])),
        "holdout_digests": dict(report.get("holdout_digests") or {}),
    }


def read_revisions(*, path: Path) -> dict[str, HoldoutRevision]:
    """Load the accepted holdout edits, keyed ``<corpus>/<path>``; empty when absent."""
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_file_digest(*, content: bytes, entry: FileEntry | None) -> str:
    """Return the SHA-256 of a file's content together with the truth of its labels.

    Flipping a flag or dropping a label changes the digest as surely as editing
    the file, so neither can slip past the gate.
    """
    if entry is None:
        truth: object = None
    elif "labels" in entry:
        truth = sorted([label["line"], label["flag"]] for label in entry["labels"])
    else:
        truth = entry.get("flag")
    labels = json.dumps(truth, sort_keys=True).encode("utf-8")
    return hashlib.sha256(content + b"\0labels\0" + labels).hexdigest()


def build_holdout_digests(*, corpora_root: Path) -> HoldoutDigests:
    """Return the digest of every holdout file of every corpus under *corpora_root*."""
    digests: HoldoutDigests = {}
    for corpus in sorted(path for path in corpora_root.iterdir() if path.is_dir()):
        if not (corpus / "labels.json").is_file():
            continue
        entries = read_labels(corpus=corpus)["files"]
        digests[corpus.name] = {
            path: build_file_digest(content=(corpus / path).read_bytes(), entry=entries.get(path))
            for path in find_corpus_files(corpus=corpus)
            if path.startswith("holdout/")
        }
    return digests


def find_holdout_changes(
    *,
    baseline: HoldoutDigests,
    current: HoldoutDigests,
    accepted: dict[str, HoldoutRevision],
) -> list[str]:
    """List the baseline holdout files that are gone or changed, bar accepted edits."""
    changes: list[str] = []
    for corpus, files in sorted(baseline.items()):
        now = current.get(corpus, {})
        for path, digest in sorted(files.items()):
            key = f"{corpus}/{path}"
            new = now.get(path)
            if new == digest or (key in accepted and accepted[key].get("digest") == new):
                continue
            what = "was removed" if new is None else "changed (content or labels)"
            changes.append(f"{key}: holdout file {what} since the baseline")
    return changes


def read_corpus_name(*, record: ScoreRecord) -> str:
    """Return the corpus directory name a record was scored on."""
    return record.get("corpus", "").rsplit("/", 1)[-1]


def select_best_baseline(
    *,
    history: list[AuditRecord],
    digests: HoldoutDigests,
) -> tuple[list[ScoreRecord], list[str]]:
    """Return one record per rule with its best comparable holdout numbers, and the skips.

    An audit's record is comparable when that audit recorded exactly the
    holdout files this run scores for the rule's corpus. The best precision and
    the best recall are taken independently, over every comparable audit.
    """
    best: dict[str, Metrics] = {}
    seen: set[str] = set()
    for audit in history:
        for record in audit.get("accuracy", []):
            if "error" in record or record.get("holdout") is None:
                continue
            rule, corpus = record["rule"], read_corpus_name(record=record)
            seen.add(rule)
            if audit.get("holdout_digests", {}).get(corpus) != digests.get(corpus):
                continue
            merged = best.setdefault(rule, {"precision": None, "recall": None})
            for metric in GATED_METRICS:
                pair = (merged[metric], record["holdout"].get(metric))
                merged[metric] = max((v for v in pair if v is not None), default=None)
    skipped = [
        f"{rule}: holdout files changed since every audit that scored it"
        for rule in sorted(seen - set(best))
    ]
    records: list[ScoreRecord] = [
        {"rule": rule, "holdout": metrics} for rule, metrics in sorted(best.items())
    ]
    return records, skipped


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
    gated: list[str] = []
    regressions: list[str] = []
    skipped: list[str] = []
    for rule, previous in sorted(index_by_rule(records=baseline).items()):
        if "error" in previous or previous.get("holdout") is None:
            skipped.append(f"{rule}: no holdout numbers in the baseline")
            continue
        gated.append(rule)
        regressions.extend(
            compare_rule(rule=rule, previous=previous, current=now.get(rule), tolerance=tolerance),
        )
    return {
        "baseline": baseline_name,
        "tolerance": tolerance,
        "gated": gated,
        "regressions": regressions,
        "holdout_changes": [],
        "skipped": skipped,
        "notes": [],
    }


def apply_history_gate(
    *,
    history: list[Path],
    current: AuditRecord,
    accepted: dict[str, HoldoutRevision],
    tolerance: float = DEFAULT_TOLERANCE,
) -> GateOutcome:
    """Gate a run's holdout numbers and files against the audits in *history*.

    *current* holds this run's accuracy records and holdout digests. The
    numbers are held to the best comparable audit in *history*; the files to
    the newest audit in *history* that recorded holdout digests.
    """
    digests = current["holdout_digests"]
    audits = [read_audit(path=path) for path in history]
    best, skipped = select_best_baseline(history=audits, digests=digests)
    newest = history[-1].name if history else "no audit"
    outcome = find_regressions(
        baseline=best,
        current=current["accuracy"],
        tolerance=tolerance,
        baseline_name=f"the best of {len(history)} audit(s), newest {newest}",
    )
    outcome["skipped"].extend(skipped)
    recorded = [audit["holdout_digests"] for audit in audits if audit.get("holdout_digests")]
    if recorded:
        outcome["holdout_changes"] = find_holdout_changes(
            baseline=recorded[-1],
            current=digests,
            accepted=accepted,
        )
    else:
        outcome["notes"].append(
            "no baseline records holdout digests, so holdout edits were not checked",
        )
    if not outcome["gated"]:
        outcome["notes"].append(
            f"no rule was gated: none of the {len(history)} baseline audit(s) records holdout "
            "numbers for the holdout files this run scores (an audit written before the dev "
            "and holdout split has none); the next release audit records the first baseline",
        )
    return outcome


def format_gate(*, outcome: GateOutcome) -> list[str]:
    """Render the gate's failures, notes and a one-line tally for the audit summary."""
    lines = [f"gate: HOLDOUT EDIT {change}" for change in outcome["holdout_changes"]]
    lines.extend(f"gate: REGRESSION {regression}" for regression in outcome["regressions"])
    lines.extend(f"gate: SKIPPED {skip}" for skip in outcome["skipped"])
    lines.extend(f"gate: NOTE {note}" for note in outcome["notes"])
    lines.append(
        f"gate: {len(outcome['gated'])} rule(s) gated, {len(outcome['regressions'])} "
        f"regression(s), {len(outcome['holdout_changes'])} holdout edit(s), "
        f"{len(outcome['skipped'])} rule(s) skipped, against {outcome['baseline']}",
    )
    return lines


def is_failing(*, outcome: GateOutcome | None) -> bool:
    """True when the gate found a regression or an unaccepted holdout edit."""
    return bool(outcome and (outcome["regressions"] or outcome["holdout_changes"]))
