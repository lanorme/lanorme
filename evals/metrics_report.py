"""Human-readable reports of labelled-corpus scores.

A scorer run directly prints ``format_report`` (dev, holdout, generated, gap,
then the misclassified sites); ``audit.py`` prints ``format_summary_line`` once
per rule. Kept apart from ``labelled_corpus`` so the scoring stays small.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from labelled_corpus import Evaluation, Metrics, ScoreRecord, Site, evaluate_corpus


def format_ratio(*, value: float | None) -> str:
    """Render a ratio to three places, or ``n/a`` when it is undefined."""
    return "n/a" if value is None else f"{value:.3f}"


def format_metrics(*, label: str, metrics: Metrics | None) -> str:
    """Render one split's metrics on a line."""
    if metrics is None:
        return f"  {label:<17} (no files in this split)"
    return (
        f"  {label:<17} P={format_ratio(value=metrics['precision'])} "
        f"R={format_ratio(value=metrics['recall'])} F1={format_ratio(value=metrics['f1'])}  "
        f"TP={metrics['tp']} FP={metrics['fp']} FN={metrics['fn']} TN={metrics['tn']}"
    )


def format_sites(*, title: str, sites: list[Site], notes: dict[Site, str]) -> list[str]:
    """Render a list of misclassified sites with their notes."""
    lines = [f"{title} ({len(sites)})"]
    for path, line in sites:
        where = path if line == 0 else f"{path}:{line}"
        lines.append(f"  {where}  {notes.get((path, line), '')}".rstrip())
    if not sites:
        lines.append("  (none)")
    return lines


def format_report(*, evaluation: Evaluation) -> str:
    """Render the human-readable report a scorer prints when run directly."""
    record = evaluation.record
    lines = [
        f"{record['rule']} against {record['corpus']} (split: {record['split']})",
        format_metrics(label="dev", metrics=record["dev"]),
        format_metrics(label="holdout", metrics=record["holdout"]),
    ]
    if "holdout_generated" in record:
        lines.append(
            format_metrics(label="holdout generated", metrics=record["holdout_generated"]),
        )
    gap = record["gap"]
    if gap is not None:
        lines.append(
            f"  {'gap (dev-holdout)':<17} P={format_ratio(value=gap['precision'])} "
            f"R={format_ratio(value=gap['recall'])} F1={format_ratio(value=gap['f1'])}",
        )
    lines.append("")
    lines.extend(
        format_sites(
            title="FALSE POSITIVES",
            sites=evaluation.false_positives,
            notes=evaluation.notes,
        ),
    )
    lines.extend(
        format_sites(
            title="FALSE NEGATIVES",
            sites=evaluation.false_negatives,
            notes=evaluation.notes,
        ),
    )
    return "\n".join(lines)


def run_scorer(*, rule: str, corpus_name: str, find_flagged: Callable[[Path], set[Site]]) -> int:
    """Print a scorer's report and return its exit code (2 on a stale corpus)."""
    try:
        evaluation = evaluate_corpus(rule=rule, corpus_name=corpus_name, find_flagged=find_flagged)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    print(format_report(evaluation=evaluation))
    return 0


def format_split_ratios(*, record: ScoreRecord, split: str) -> str:
    """Render one split's precision and recall, or ``n/a`` when it has none."""
    metrics = record.get(split)
    if metrics is None:
        return f"{split} n/a"
    precision = format_ratio(value=metrics["precision"])
    return f"{split} P={precision} R={format_ratio(value=metrics['recall'])}"


def format_summary_line(*, record: ScoreRecord) -> str:
    """Render one rule's audit line: dev, holdout and the gap, or its error."""
    rule = record.get("rule", "?")
    if "error" in record:
        return f"{rule}: ERROR {record['error']}"
    parts = [format_split_ratios(record=record, split=split) for split in ("dev", "holdout")]
    gap = record.get("gap")
    if gap is not None:
        parts.append(
            f"gap P={format_ratio(value=gap['precision'])} R={format_ratio(value=gap['recall'])}",
        )
    return f"{rule}: " + " | ".join(parts)
