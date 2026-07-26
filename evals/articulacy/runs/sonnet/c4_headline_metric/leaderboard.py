"""leaderboard.py -- headline accuracy leaderboard for a model-eval JSONL log.

Turns a JSONL file of per-example scoring records into:

  * one headline accuracy per model, with a 95% confidence interval
  * a per-subject accuracy breakdown for each model
  * a paired significance test (``compare``) between any two models
  * a rendered Markdown leaderboard, ranked by the headline number

Input format
------------
Each line is a JSON object::

    {"example_id": "...", "subject": "...", "model": "...", "correct": 0 | 1}

``subject`` is sometimes absent entirely -- some examples in the source data
have no subject field at all. ``example_id`` and ``model`` are required;
``correct`` must be 0 or 1 (or a bool, which is coerced to 0/1).

Design notes (see REPORT.md for the full rationale)
----------------------------------------------------
Subjects are wildly unbalanced (roughly 90 to 12,000 examples per subject).
A single pooled ("micro") average would be dominated by whichever subject
happens to be largest, which is usually not what a leaderboard wants to
reward. So the **headline** number is the *macro* average: the unweighted
mean of each subject's own accuracy. The simple pooled accuracy is also
computed and shown alongside it (as ``overall_accuracy``) for context, but it
is not what the models are ranked on.

Examples with no ``subject`` cannot be assigned to a subject group, so they
are excluded from the macro/headline computation, but they *are* included in
the overall pooled accuracy and are surfaced as their own row (labelled
``(no subject)``) in the per-subject breakdown so nothing is silently dropped.

Confidence intervals are computed with closed-form formulas (Wilson score
interval for a single proportion; an Agresti-Coull-adjusted variance
combination for the macro mean). No resampling or random seeding is
involved anywhere in this module, so a rerun on the same input file always
produces bit-identical numbers -- there is nothing stochastic to seed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

# Two-sided 95% normal quantile (z such that P(-z < Z < z) = 0.95).
Z_95 = 1.959963984540054

# Sentinel used internally for "this example had no subject field".
NO_SUBJECT = "(no subject)"


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    """One scored example for one model."""

    example_id: str
    subject: str | None  # None means the input row had no "subject" field
    model: str
    correct: int  # 0 or 1


def load_records(path: str | Path) -> list[Record]:
    """Parse a JSONL file of scoring records.

    Raises ``ValueError`` (with the offending line number) if a line is not
    valid JSON, is missing a required field, or has a ``correct`` value that
    is not 0/1/bool.
    """
    records: list[Record] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: not valid JSON") from exc

            records.append(_record_from_json(obj, path, line_number))
    return records


def _record_from_json(obj: dict, path: str | Path, line_number: int) -> Record:
    for field_name in ("example_id", "model", "correct"):
        if field_name not in obj:
            raise ValueError(
                f"{path}:{line_number}: missing required field {field_name!r}"
            )

    correct_raw = obj["correct"]
    if isinstance(correct_raw, bool):
        correct = int(correct_raw)
    elif correct_raw in (0, 1):
        correct = int(correct_raw)
    else:
        raise ValueError(
            f"{path}:{line_number}: 'correct' must be 0 or 1, got {correct_raw!r}"
        )

    subject = obj.get("subject")
    if subject is not None and not isinstance(subject, str):
        subject = str(subject)

    return Record(
        example_id=str(obj["example_id"]),
        subject=subject,
        model=str(obj["model"]),
        correct=correct,
    )


# --------------------------------------------------------------------------
# Statistics helpers (closed-form, no randomness, no scipy)
# --------------------------------------------------------------------------


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Preferred over the naive normal approximation because it stays inside
    [0, 1] and behaves sensibly even for small n or accuracy near 0 or 1
    (both of which occur here: subjects range from ~90 to ~12,000 examples).
    """
    if n == 0:
        return (0.0, 1.0)
    p_hat = successes / n
    denom = 1 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    margin = (
        z * math.sqrt((p_hat * (1 - p_hat) / n) + (z * z / (4 * n * n))) / denom
    )
    lo = max(0.0, center - margin)
    hi = min(1.0, center + margin)
    return (lo, hi)


def _agresti_coull_variance(successes: int, n: int, z: float = Z_95) -> float:
    """Agresti-Coull adjusted variance estimate for a single proportion.

    Used only as a variance *input* to the macro-average CI below, so that a
    subject with a 0% or 100% observed accuracy doesn't contribute a
    degenerate zero variance to the combined interval.
    """
    if n == 0:
        return 0.0
    n_tilde = n + z * z
    p_tilde = (successes + z * z / 2) / n_tilde
    return p_tilde * (1 - p_tilde) / n_tilde


def macro_mean_interval(
    per_group_successes: Sequence[int],
    per_group_n: Sequence[int],
    z: float = Z_95,
) -> tuple[float, float]:
    """95% CI for the unweighted mean of several independent proportions.

    Each group's variance is estimated with the Agresti-Coull adjustment and
    the group variances are summed (groups are independent -- distinct sets
    of examples) and scaled by 1/K^2 for the mean of K groups.
    """
    k = len(per_group_n)
    if k == 0:
        return (0.0, 1.0)
    macro_mean = sum(s / n for s, n in zip(per_group_successes, per_group_n)) / k
    total_var = sum(
        _agresti_coull_variance(s, n, z)
        for s, n in zip(per_group_successes, per_group_n)
    )
    macro_var = total_var / (k * k)
    margin = z * math.sqrt(macro_var)
    lo = max(0.0, macro_mean - margin)
    hi = min(1.0, macro_mean + margin)
    return (lo, hi)


def _standard_normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def mcnemar_p_value(n_a_only: int, n_b_only: int) -> float:
    """Two-sided McNemar test p-value for paired binary outcomes.

    ``n_a_only`` is the count of examples where model A was correct and
    model B was not; ``n_b_only`` is the reverse. Ties (both right or both
    wrong) carry no information for this test and are not passed in.

    Uses the exact binomial form for small discordant-pair counts (n <= 200)
    and the continuity-corrected normal approximation otherwise. Both are
    closed-form / deterministic -- no simulation involved.
    """
    n = n_a_only + n_b_only
    if n == 0:
        return 1.0

    if n <= 200:
        k = min(n_a_only, n_b_only)
        cumulative = sum(math.comb(n, i) for i in range(k + 1))
        p_one_side = cumulative / (2**n)
        return min(1.0, 2 * p_one_side)

    z = (abs(n_a_only - n_b_only) - 1) / math.sqrt(n)
    p = 2 * (1 - _standard_normal_cdf(z))
    return min(1.0, max(0.0, p))


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SubjectScore:
    subject: str
    n: int
    correct: int
    accuracy: float
    ci_low: float
    ci_high: float
    counts_toward_headline: bool


@dataclass(frozen=True)
class ModelScore:
    model: str
    n_total: int
    n_missing_subject: int
    overall_accuracy: float  # micro: simple pooled accuracy, all examples
    overall_ci: tuple[float, float]
    headline_accuracy: float  # macro: unweighted mean over real subjects
    headline_ci: tuple[float, float]
    n_subjects: int
    subjects: tuple[SubjectScore, ...]  # sorted by subject name; no-subject last


def score_model(records: Sequence[Record]) -> ModelScore:
    """Compute the headline/overall accuracy and per-subject breakdown for
    the (single-model) sequence of records passed in."""
    if not records:
        raise ValueError("score_model() called with no records")

    model = records[0].model

    by_subject: dict[str, list[int]] = defaultdict(list)
    for rec in records:
        key = rec.subject if rec.subject is not None else NO_SUBJECT
        by_subject[key].append(rec.correct)

    correct_arr = np.array([r.correct for r in records], dtype=np.int64)
    n_total = int(correct_arr.size)
    total_correct = int(correct_arr.sum())
    overall_accuracy = total_correct / n_total
    overall_ci = wilson_interval(total_correct, n_total)

    real_subjects = sorted(s for s in by_subject if s != NO_SUBJECT)
    subject_scores: list[SubjectScore] = []

    headline_successes: list[int] = []
    headline_n: list[int] = []

    for subject in real_subjects:
        outcomes = np.array(by_subject[subject], dtype=np.int64)
        n = int(outcomes.size)
        k = int(outcomes.sum())
        acc = k / n
        lo, hi = wilson_interval(k, n)
        subject_scores.append(
            SubjectScore(
                subject=subject,
                n=n,
                correct=k,
                accuracy=acc,
                ci_low=lo,
                ci_high=hi,
                counts_toward_headline=True,
            )
        )
        headline_successes.append(k)
        headline_n.append(n)

    n_missing_subject = len(by_subject.get(NO_SUBJECT, []))
    if n_missing_subject:
        outcomes = np.array(by_subject[NO_SUBJECT], dtype=np.int64)
        n = int(outcomes.size)
        k = int(outcomes.sum())
        lo, hi = wilson_interval(k, n)
        subject_scores.append(
            SubjectScore(
                subject=NO_SUBJECT,
                n=n,
                correct=k,
                accuracy=k / n,
                ci_low=lo,
                ci_high=hi,
                counts_toward_headline=False,
            )
        )

    if not headline_n:
        raise ValueError(
            f"model {model!r} has no examples with a subject; "
            "cannot compute a headline (macro) accuracy for it"
        )

    headline_accuracy = sum(
        s / n for s, n in zip(headline_successes, headline_n)
    ) / len(headline_n)
    headline_ci = macro_mean_interval(headline_successes, headline_n)

    return ModelScore(
        model=model,
        n_total=n_total,
        n_missing_subject=n_missing_subject,
        overall_accuracy=overall_accuracy,
        overall_ci=overall_ci,
        headline_accuracy=headline_accuracy,
        headline_ci=headline_ci,
        n_subjects=len(real_subjects),
        subjects=tuple(subject_scores),
    )


def score_all(records: Sequence[Record]) -> dict[str, ModelScore]:
    """Group records by model and score each one. Returns a dict keyed by
    model name; iterate it in sorted order (or use ``rank_models``) for a
    deterministic display order."""
    by_model: dict[str, list[Record]] = defaultdict(list)
    for rec in records:
        by_model[rec.model].append(rec)
    return {model: score_model(recs) for model, recs in by_model.items()}


def rank_models(scores: Mapping[str, ModelScore]) -> list[ModelScore]:
    """Rank models by headline accuracy, descending. Ties are broken by
    model name so the ordering is stable across reruns."""
    return sorted(
        scores.values(), key=lambda s: (-s.headline_accuracy, s.model)
    )


# --------------------------------------------------------------------------
# Pairwise comparison
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ComparisonResult:
    model_a: str
    model_b: str
    n_common: int
    n_a_only_dropped: int  # examples model A had that model B did not
    n_b_only_dropped: int  # examples model B had that model A did not
    accuracy_a: float  # accuracy_a/b are over the *common* example set only
    accuracy_b: float
    diff: float  # accuracy_a - accuracy_b
    n_a_correct_b_wrong: int
    n_b_correct_a_wrong: int
    p_value: float
    significant: bool  # at alpha
    alpha: float

    def summary(self) -> str:
        if self.n_common == 0:
            return (
                f"{self.model_a} and {self.model_b} share no example_ids; "
                "cannot compare."
            )
        p_str = "< 1e-300" if self.p_value == 0.0 else f"{self.p_value:.4g}"
        if not self.significant:
            return (
                f"No significant difference between {self.model_a} "
                f"({self.accuracy_a:.3%}) and {self.model_b} "
                f"({self.accuracy_b:.3%}) at alpha={self.alpha} "
                f"(p={p_str})."
            )
        better, worse = (
            (self.model_a, self.model_b)
            if self.diff > 0
            else (self.model_b, self.model_a)
        )
        return (
            f"{better} is significantly better than {worse} at "
            f"alpha={self.alpha} (p={p_str})."
        )


def compare(
    model_a: str,
    model_b: str,
    records: Sequence[Record],
    alpha: float = 0.05,
) -> ComparisonResult:
    """Paired significance test between two models scored on the same
    examples, using McNemar's test on the discordant pairs.

    Only ``example_id``s present for *both* models are used. If the two
    models were not in fact scored on identical example sets, the dropped
    counts are reported in the result rather than raising, so the operator
    can see (and judge) how large the mismatch is.
    """
    outcomes_a: dict[str, int] = {}
    outcomes_b: dict[str, int] = {}
    for rec in records:
        if rec.model == model_a:
            outcomes_a[rec.example_id] = rec.correct
        elif rec.model == model_b:
            outcomes_b[rec.example_id] = rec.correct

    common_ids = sorted(outcomes_a.keys() & outcomes_b.keys())
    n_common = len(common_ids)
    n_a_only_dropped = len(outcomes_a.keys() - outcomes_b.keys())
    n_b_only_dropped = len(outcomes_b.keys() - outcomes_a.keys())

    if n_common == 0:
        return ComparisonResult(
            model_a=model_a,
            model_b=model_b,
            n_common=0,
            n_a_only_dropped=n_a_only_dropped,
            n_b_only_dropped=n_b_only_dropped,
            accuracy_a=float("nan"),
            accuracy_b=float("nan"),
            diff=float("nan"),
            n_a_correct_b_wrong=0,
            n_b_correct_a_wrong=0,
            p_value=1.0,
            significant=False,
            alpha=alpha,
        )

    a_vals = np.array([outcomes_a[eid] for eid in common_ids], dtype=np.int64)
    b_vals = np.array([outcomes_b[eid] for eid in common_ids], dtype=np.int64)

    n_a_correct_b_wrong = int(np.sum((a_vals == 1) & (b_vals == 0)))
    n_b_correct_a_wrong = int(np.sum((b_vals == 1) & (a_vals == 0)))

    p_value = mcnemar_p_value(n_a_correct_b_wrong, n_b_correct_a_wrong)

    accuracy_a = float(a_vals.mean())
    accuracy_b = float(b_vals.mean())

    return ComparisonResult(
        model_a=model_a,
        model_b=model_b,
        n_common=n_common,
        n_a_only_dropped=n_a_only_dropped,
        n_b_only_dropped=n_b_only_dropped,
        accuracy_a=accuracy_a,
        accuracy_b=accuracy_b,
        diff=accuracy_a - accuracy_b,
        n_a_correct_b_wrong=n_a_correct_b_wrong,
        n_b_correct_a_wrong=n_b_correct_a_wrong,
        p_value=p_value,
        significant=p_value < alpha,
        alpha=alpha,
    )


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------


def _fmt_pct(x: float) -> str:
    return f"{x:.2%}"


def _fmt_ci(ci: tuple[float, float]) -> str:
    return f"[{ci[0]:.2%}, {ci[1]:.2%}]"


def render_markdown(scores: Mapping[str, ModelScore]) -> str:
    """Render the ranked leaderboard plus a per-model subject breakdown."""
    ranked = rank_models(scores)
    lines: list[str] = []

    lines.append("# Leaderboard")
    lines.append("")
    lines.append(
        "Headline accuracy is the **macro** average: the unweighted mean of "
        "each model's own per-subject accuracies, so no single large subject "
        "dominates the ranking. Overall accuracy (simple pooled mean over "
        "every example) is shown alongside it for context but is not the "
        "ranking key."
    )
    lines.append("")
    lines.append(
        "| Rank | Model | Headline (macro) | 95% CI | Overall (micro) | "
        "95% CI | N examples | N subjects |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for rank, score in enumerate(ranked, start=1):
        lines.append(
            f"| {rank} | {score.model} | {_fmt_pct(score.headline_accuracy)} "
            f"| {_fmt_ci(score.headline_ci)} | {_fmt_pct(score.overall_accuracy)} "
            f"| {_fmt_ci(score.overall_ci)} | {score.n_total} | "
            f"{score.n_subjects} |"
        )
    lines.append("")

    for score in ranked:
        lines.append(f"## {score.model} -- per-subject breakdown")
        lines.append("")
        lines.append("| Subject | N | Accuracy | 95% CI | Counts toward headline |")
        lines.append("|---|---|---|---|---|")
        for subj in score.subjects:
            counts = "yes" if subj.counts_toward_headline else "no"
            lines.append(
                f"| {subj.subject} | {subj.n} | {_fmt_pct(subj.accuracy)} "
                f"| {_fmt_ci((subj.ci_low, subj.ci_high))} | {counts} |"
            )
        lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Path to the JSONL results file")
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("MODEL_A", "MODEL_B"),
        help="Also print a pairwise significance test between two models",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.05,
        help="Significance threshold for --compare (default: 0.05)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    records = load_records(args.input)
    scores = score_all(records)
    print(render_markdown(scores))

    if args.compare:
        model_a, model_b = args.compare
        result = compare(model_a, model_b, records, alpha=args.alpha)
        print("\n## Pairwise comparison\n")
        print(result.summary())

    return 0


if __name__ == "__main__":
    sys.exit(main())
