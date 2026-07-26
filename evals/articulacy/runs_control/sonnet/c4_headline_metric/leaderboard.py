"""Build a headline-accuracy leaderboard from per-example model results.

Input is a JSONL file where each line is an object::

    {"example_id": ..., "subject": ..., "model": ..., "correct": 0 | 1}

``subject`` is optional (some examples carry no subject at all) and the
subjects present are heavily unbalanced: one subject may account for
thousands of examples while another has only a few dozen. A model's
"headline" number is therefore *not* the plain pooled accuracy over all
rows -- that would let the largest subject dominate the score. Instead the
headline is the *macro-average* of per-subject accuracies: every subject
counts once, regardless of how many examples it contributed. Rows with no
``subject`` are grouped into a single "(none)" bucket and counted as one
more subject in that average.

Design notes on the two statistical pieces:

* Headline confidence interval. The headline is a simple mean of K
  (assumed independent, since they are disjoint groups of examples)
  subject-level proportions. Its variance is therefore the average of the
  subject-level binomial variances, scaled by 1/K, and the interval is the
  usual normal approximation around that mean. Per-subject intervals in the
  breakdown use the Wilson score interval instead, since several subjects
  are small enough (~90 examples) that the plain normal approximation can
  produce nonsensical bounds outside [0, 1].

* ``compare(model_a, model_b)``. Because both models are scored on the same
  examples, the right test is a paired one: McNemar's test on the 2x2 table
  of (model_a correct, model_b correct) pairs. Only the discordant cells
  (exactly one model right) matter under the null that both models are
  equally likely to be the one that's right. The exact binomial form is
  used when the discordant count is small, and the standard
  continuity-corrected chi-square approximation otherwise.

Reproducibility. Nothing here is randomised (no bootstrap, no sampling), so
a rerun on the same input is deterministic by construction. The one thing
that *could* silently vary between runs is summation order (dict/set
iteration order is not guaranteed stable across Python processes), which
can perturb the last bit of a floating-point sum. All sums that feed the
headline number use ``math.fsum``, which is exact and therefore
order-independent, so that risk is eliminated rather than just made
unlikely.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

UNKNOWN_SUBJECT = "(none)"

# Exact two-sided 97.5th percentile of the standard normal distribution,
# i.e. the z-value for a 95% confidence interval.
Z_95 = 1.959963984540054

ALPHA = 0.05


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    """One scored example for one model."""

    example_id: str
    subject: str
    model: str
    correct: int


def _parse_line(line: str, line_no: int) -> Record | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"line {line_no}: invalid JSON ({exc})") from exc

    if "example_id" not in obj or obj["example_id"] is None:
        raise ValueError(f"line {line_no}: missing 'example_id'")
    if "model" not in obj or not obj["model"]:
        raise ValueError(f"line {line_no}: missing 'model'")
    if "correct" not in obj:
        raise ValueError(f"line {line_no}: missing 'correct'")

    correct = obj["correct"]
    if correct not in (0, 1):
        raise ValueError(
            f"line {line_no}: 'correct' must be 0 or 1, got {correct!r}"
        )

    subject = obj.get("subject") or UNKNOWN_SUBJECT

    return Record(
        example_id=str(obj["example_id"]),
        subject=str(subject),
        model=str(obj["model"]),
        correct=int(correct),
    )


def iter_records(lines: Iterable[str]) -> Iterator[Record]:
    """Parse JSONL lines into ``Record``s, skipping blank lines."""
    for line_no, line in enumerate(lines, start=1):
        record = _parse_line(line, line_no)
        if record is not None:
            yield record


def load_records(path: str | Path) -> list[Record]:
    """Read and parse a JSONL results file."""
    with open(path, encoding="utf-8") as handle:
        return list(iter_records(handle))


def _dedupe(records: Iterable[Record]) -> list[Record]:
    """Collapse duplicate (model, example_id) rows, keeping the last one.

    Returned in a sort order fixed by (model, example_id) so that later
    aggregation does not depend on the incidental order records arrived in.
    """
    latest: dict[tuple[str, str], Record] = {}
    for record in records:
        latest[(record.model, record.example_id)] = record
    return sorted(latest.values(), key=lambda r: (r.model, r.example_id))


# --------------------------------------------------------------------------
# Confidence intervals
# --------------------------------------------------------------------------


def _wilson_interval(k: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion k/n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = (z * math.sqrt((p * (1 - p) / n) + (z2 / (4 * n * n)))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _wald_variance(p: float, n: int) -> float:
    if n == 0:
        return 0.0
    return p * (1 - p) / n


# --------------------------------------------------------------------------
# Per-model / per-subject aggregation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SubjectStats:
    subject: str
    n: int
    correct: int
    accuracy: float
    ci_low: float
    ci_high: float


@dataclass(frozen=True)
class ModelReport:
    model: str
    n_examples: int
    n_subjects: int
    headline_accuracy: float
    ci_low: float
    ci_high: float
    subjects: list[SubjectStats] = field(default_factory=list)


def _subject_counts(records: list[Record]) -> dict[str, tuple[int, int]]:
    """{subject: (num_correct, num_examples)} for one model's records."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for record in records:
        bucket = counts[record.subject]
        bucket[0] += record.correct
        bucket[1] += 1
    return {subject: (k, n) for subject, (k, n) in counts.items()}


def _build_model_report(model: str, records: list[Record]) -> ModelReport:
    counts = _subject_counts(records)

    subject_stats = []
    for subject in sorted(counts):
        k, n = counts[subject]
        p = k / n
        ci_low, ci_high = _wilson_interval(k, n)
        subject_stats.append(SubjectStats(subject, n, k, p, ci_low, ci_high))

    accuracies = [s.accuracy for s in subject_stats]
    n_subjects = len(subject_stats)
    headline = math.fsum(accuracies) / n_subjects

    # Headline variance: the average of independent per-subject variances,
    # since the headline is an unweighted mean of n_subjects independent
    # proportions.
    variance = (
        math.fsum(_wald_variance(s.accuracy, s.n) for s in subject_stats)
        / (n_subjects * n_subjects)
    )
    se = math.sqrt(variance)
    ci_low = max(0.0, headline - Z_95 * se)
    ci_high = min(1.0, headline + Z_95 * se)

    return ModelReport(
        model=model,
        n_examples=len(records),
        n_subjects=n_subjects,
        headline_accuracy=headline,
        ci_low=ci_low,
        ci_high=ci_high,
        subjects=subject_stats,
    )


# --------------------------------------------------------------------------
# Pairwise comparison (McNemar's test)
# --------------------------------------------------------------------------


def _binom_cdf_at_half(k: int, n: int) -> float:
    """P(X <= k) for X ~ Binomial(n, 0.5), computed exactly."""
    total = math.fsum(math.comb(n, i) for i in range(k + 1))
    return total / (2**n)


def _exact_mcnemar_p(k: int, n: int) -> float:
    # Binomial(n, 0.5) is symmetric, so the two-sided exact p-value is just
    # twice the smaller tail.
    return min(1.0, 2.0 * _binom_cdf_at_half(k, n))


def _chi2_sf_df1(statistic: float) -> float:
    """P(chi2_1 >= statistic), via the standard normal relation chi2_1 = Z^2."""
    return math.erfc(math.sqrt(statistic / 2.0))


def _mcnemar_test(n_a_only: int, n_b_only: int) -> tuple[float, float, str]:
    """McNemar's test on the discordant-pair counts.

    n_a_only: examples where A was correct and B was not.
    n_b_only: examples where B was correct and A was not.
    Returns (statistic, p_value, method).
    """
    n = n_a_only + n_b_only
    if n == 0:
        return (0.0, 1.0, "no discordant pairs")
    if n < 25:
        k = min(n_a_only, n_b_only)
        return (float(k), _exact_mcnemar_p(k, n), "exact binomial")
    statistic = (abs(n_a_only - n_b_only) - 1) ** 2 / n
    return (statistic, _chi2_sf_df1(statistic), "chi-square (continuity-corrected)")


@dataclass(frozen=True)
class ComparisonResult:
    model_a: str
    model_b: str
    n_common: int
    n_only_in_a: int
    n_only_in_b: int
    accuracy_a: float
    accuracy_b: float
    both_correct: int
    both_wrong: int
    a_only_correct: int
    b_only_correct: int
    statistic: float
    p_value: float
    method: str
    alpha: float = ALPHA

    @property
    def significant(self) -> bool:
        return self.p_value < self.alpha

    @property
    def summary(self) -> str:
        verdict = "a significant" if self.significant else "no significant"
        return (
            f"{self.model_a} ({self.accuracy_a:.2%}) vs {self.model_b} "
            f"({self.accuracy_b:.2%}) on {self.n_common} shared examples: "
            f"{verdict} difference (p={self.p_value:.4g}, {self.method})."
        )


# --------------------------------------------------------------------------
# Leaderboard
# --------------------------------------------------------------------------


class Leaderboard:
    """Headline accuracy, per-subject breakdown, and pairwise comparisons."""

    def __init__(self, records: Iterable[Record]):
        self._records = _dedupe(records)

        by_model: dict[str, list[Record]] = defaultdict(list)
        for record in self._records:
            by_model[record.model].append(record)
        self._by_model = dict(by_model)

        self._by_model_example: dict[str, dict[str, int]] = {
            model: {r.example_id: r.correct for r in recs}
            for model, recs in self._by_model.items()
        }

        self.reports: dict[str, ModelReport] = {
            model: _build_model_report(model, recs)
            for model, recs in sorted(self._by_model.items())
        }

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "Leaderboard":
        return cls(load_records(path))

    def ranked(self) -> list[ModelReport]:
        """Models ranked by headline accuracy, descending; ties broken by name."""
        return sorted(
            self.reports.values(),
            key=lambda r: (-r.headline_accuracy, r.model),
        )

    def compare(self, model_a: str, model_b: str) -> ComparisonResult:
        """Is the accuracy difference between two models significant?

        Assumes both models were scored on (mostly) the same examples;
        the comparison is restricted to the examples they have in common,
        and the sizes of any leftovers are reported for transparency.
        """
        if model_a not in self._by_model_example:
            raise KeyError(f"unknown model: {model_a!r}")
        if model_b not in self._by_model_example:
            raise KeyError(f"unknown model: {model_b!r}")

        a_scores = self._by_model_example[model_a]
        b_scores = self._by_model_example[model_b]
        common = sorted(set(a_scores) & set(b_scores))
        if not common:
            raise ValueError(
                f"{model_a!r} and {model_b!r} share no scored examples"
            )

        both_correct = both_wrong = a_only = b_only = 0
        for example_id in common:
            a_correct = a_scores[example_id]
            b_correct = b_scores[example_id]
            if a_correct and b_correct:
                both_correct += 1
            elif not a_correct and not b_correct:
                both_wrong += 1
            elif a_correct and not b_correct:
                a_only += 1
            else:
                b_only += 1

        n_common = len(common)
        statistic, p_value, method = _mcnemar_test(a_only, b_only)

        return ComparisonResult(
            model_a=model_a,
            model_b=model_b,
            n_common=n_common,
            n_only_in_a=len(a_scores) - n_common,
            n_only_in_b=len(b_scores) - n_common,
            accuracy_a=(both_correct + a_only) / n_common,
            accuracy_b=(both_correct + b_only) / n_common,
            both_correct=both_correct,
            both_wrong=both_wrong,
            a_only_correct=a_only,
            b_only_correct=b_only,
            statistic=statistic,
            p_value=p_value,
            method=method,
        )

    # ----------------------------------------------------------------
    # Rendering
    # ----------------------------------------------------------------

    def to_markdown(self, include_subject_breakdown: bool = True) -> str:
        lines = ["# Model leaderboard", ""]
        lines.append(
            "Headline accuracy is the macro-average of per-subject accuracy "
            "(each subject weighted equally), with a 95% confidence interval."
        )
        lines.append("")
        lines.append("| Rank | Model | Accuracy | 95% CI | Examples | Subjects |")
        lines.append("|---:|---|---:|---|---:|---:|")
        for rank, report in enumerate(self.ranked(), start=1):
            lines.append(
                f"| {rank} | {report.model} | {report.headline_accuracy:.2%} "
                f"| [{report.ci_low:.2%}, {report.ci_high:.2%}] "
                f"| {report.n_examples} | {report.n_subjects} |"
            )

        if include_subject_breakdown:
            lines.append("")
            lines.append("## Per-subject breakdown")
            for report in self.ranked():
                lines.append("")
                lines.append(f"### {report.model}")
                lines.append("")
                lines.append("| Subject | N | Accuracy | 95% CI |")
                lines.append("|---|---:|---:|---|")
                for subject_stat in report.subjects:
                    lines.append(
                        f"| {subject_stat.subject} | {subject_stat.n} "
                        f"| {subject_stat.accuracy:.2%} "
                        f"| [{subject_stat.ci_low:.2%}, {subject_stat.ci_high:.2%}] |"
                    )

        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute a headline-accuracy leaderboard from per-example results."
    )
    parser.add_argument("input", type=Path, help="path to the JSONL results file")
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="write the Markdown leaderboard here instead of stdout",
    )
    parser.add_argument(
        "--compare", nargs=2, metavar=("MODEL_A", "MODEL_B"), default=None,
        help="print a significance comparison between two models instead "
        "of the leaderboard",
    )
    parser.add_argument(
        "--no-subjects", action="store_true",
        help="omit the per-subject breakdown from the leaderboard",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    try:
        board = Leaderboard.from_jsonl(args.input)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.compare:
        try:
            result = board.compare(*args.compare)
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(result.summary)
        return 0

    markdown = board.to_markdown(include_subject_breakdown=not args.no_subjects)
    if args.output:
        args.output.write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
