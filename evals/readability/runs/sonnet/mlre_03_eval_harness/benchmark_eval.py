"""benchmark_eval.py -- a small, dependency-light harness for scoring a
language model's outputs against benchmarks.

Three task shapes are supported:

* ``multiple_choice`` -- each example offers a list of candidate
  continuations; the model is scored by the log-likelihood it assigns to
  each one. Both the raw ("unnormalised") log-likelihood and a
  length-normalised log-likelihood are reported, each with its own
  accuracy. Because the ``Model`` protocol below does not expose a
  tokenizer, "length" here means the character length of the candidate
  string -- a reasonable proxy when per-token log-probabilities are not
  available.
* ``exact_match`` -- the model generates free text, which is compared to
  one or more gold answers after a fixed normalisation chain (lowercase,
  strip articles, strip punctuation, collapse whitespace).
* ``pass_at_k`` -- for code problems, the model is sampled ``n`` times per
  problem; each sample is executed against the problem's tests, and
  pass@k is computed with the unbiased estimator from Chen et al., 2021
  ("Evaluating Large Language Models Trained on Code").

Cross-cutting features:

* Few-shot prompting, with a fixed RNG seed controlling which exemplars
  are drawn from a fewshot pool, and a configurable number of shots.
* Bootstrap confidence intervals on any per-example score array, plus a
  paired bootstrap for comparing two runs over the same examples.
* Per-subject breakdowns (when examples carry a ``subject`` field), with
  the macro-average across subjects reported as the headline number.
* Disk-backed result caching keyed by (model name, benchmark name, a hash
  of the run's configuration), so re-running an already-scored
  combination is a cache hit rather than a re-score.
* Results are written out both as JSON (full detail) and as a Markdown
  summary table.

Example
-------
    config = BenchmarkConfig(
        name="arc_easy",
        task_type="multiple_choice",
        examples=examples,             # list[dict], each with "context",
                                        # "choices", "answer", "subject"
        format_example=format_arc_example,
        num_shots=5,
        seed=1234,
        fewshot_pool=train_examples,
    )
    results = run_benchmarks(model, "my-model-7b", [config])
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import string
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence

import numpy as np

# --------------------------------------------------------------------------
# Model protocol
# --------------------------------------------------------------------------


class Model(Protocol):
    """The interface every scored model must implement."""

    def loglikelihood(self, context: str, continuation: str) -> float:
        """Total log-likelihood (natural log) the model assigns to
        ``continuation`` conditioned on ``context``."""
        ...

    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate free-text continuation for ``prompt``."""
        ...


# --------------------------------------------------------------------------
# Few-shot prompting
# --------------------------------------------------------------------------

FormatExample = Callable[[Mapping[str, Any], bool], str]


class FewShotSampler:
    """Draws few-shot exemplars from a pool with a fixed seed.

    A single ``random.Random`` instance is seeded once and reused across
    calls to :meth:`sample`, so a given seed always produces the same
    sequence of exemplar sets for a given pool and ordering of examples.
    """

    def __init__(
        self, pool: Sequence[Mapping[str, Any]], num_shots: int, seed: int
    ) -> None:
        self.pool = list(pool)
        self.num_shots = max(num_shots, 0)
        self._rng = random.Random(seed)

    def sample(
        self, exclude: Mapping[str, Any] | None = None
    ) -> list[Mapping[str, Any]]:
        if self.num_shots <= 0 or not self.pool:
            return []
        candidates = [ex for ex in self.pool if ex is not exclude]
        k = min(self.num_shots, len(candidates))
        if k <= 0:
            return []
        return self._rng.sample(candidates, k)


def build_prompt(
    shots: Sequence[Mapping[str, Any]],
    example: Mapping[str, Any],
    format_example: FormatExample,
    separator: str = "\n\n",
) -> str:
    """Concatenate formatted few-shot exemplars with the target example."""
    parts = [format_example(shot, True) for shot in shots]
    parts.append(format_example(example, False))
    return separator.join(parts)


# --------------------------------------------------------------------------
# Exact match normalisation
# --------------------------------------------------------------------------

_ARTICLES_RE = re.compile(r"\b(a|an|the)\b", re.IGNORECASE)
_PUNCT_TABLE = str.maketrans("", "", string.punctuation)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_answer(text: str) -> str:
    """Normalisation chain: lowercase, strip articles, strip punctuation,
    collapse whitespace."""
    text = text.lower()
    text = _ARTICLES_RE.sub(" ", text)
    text = text.translate(_PUNCT_TABLE)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


# --------------------------------------------------------------------------
# Multiple choice scoring
# --------------------------------------------------------------------------


def score_multiple_choice(
    model: Model, example: Mapping[str, Any], prompt: str
) -> dict[str, Any]:
    """Score every choice by log-likelihood; return both the raw and the
    length-normalised prediction and correctness."""
    choices: Sequence[str] = example["choices"]
    lls = np.array(
        [model.loglikelihood(prompt, choice) for choice in choices], dtype=float
    )
    lengths = np.array([max(len(choice), 1) for choice in choices], dtype=float)
    normalized = lls / lengths

    pred_unnormalized = int(np.argmax(lls))
    pred_normalized = int(np.argmax(normalized))
    gold = int(example["answer"])

    return {
        "loglikelihoods": lls.tolist(),
        "normalized_loglikelihoods": normalized.tolist(),
        "pred_unnormalized": pred_unnormalized,
        "pred_normalized": pred_normalized,
        "gold": gold,
        "correct_unnormalized": float(pred_unnormalized == gold),
        "correct_normalized": float(pred_normalized == gold),
    }


# --------------------------------------------------------------------------
# Exact match scoring
# --------------------------------------------------------------------------


def score_exact_match(
    model: Model, example: Mapping[str, Any], prompt: str, **gen_kwargs: Any
) -> dict[str, Any]:
    prediction = model.generate(prompt, **gen_kwargs)
    gold_field = example.get("answers", example.get("answer"))
    gold_answers = gold_field if isinstance(gold_field, list) else [gold_field]

    norm_pred = normalize_answer(prediction)
    norm_golds = [normalize_answer(str(gold)) for gold in gold_answers]
    correct = float(norm_pred in norm_golds)

    return {
        "prediction": prediction,
        "normalized_prediction": norm_pred,
        "gold": gold_answers,
        "correct": correct,
    }


# --------------------------------------------------------------------------
# Pass@k scoring for code tasks
# --------------------------------------------------------------------------


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k estimator (Chen et al., 2021).

    ``n`` samples were drawn, ``c`` of them passed all tests; estimate the
    probability that at least one of ``k`` samples (drawn without
    replacement from the same ``n``) would pass.
    """
    if n - c < k:
        return 1.0
    return float(1.0 - np.prod(1.0 - k / np.arange(n - c + 1, n + 1)))


def _execute_candidate(program: str, timeout: float) -> bool:
    """Run ``program`` in a fresh subprocess; pass iff it exits zero within
    ``timeout`` seconds. Isolating execution in a subprocess keeps a
    crashing or hanging candidate from taking down the harness, though it
    is not a hardened sandbox against actively malicious code."""
    fd, path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(program)
        result = subprocess.run(
            [sys.executable, path],
            capture_output=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return False
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def score_pass_at_k(
    model: Model,
    example: Mapping[str, Any],
    prompt: str,
    n_samples: int,
    k_values: Sequence[int],
    timeout: float = 5.0,
    **gen_kwargs: Any,
) -> dict[str, Any]:
    completions = [model.generate(prompt, **gen_kwargs) for _ in range(n_samples)]
    programs = [
        f"{example['prompt']}{completion}\n{example['test']}"
        for completion in completions
    ]
    outcomes = [_execute_candidate(program, timeout) for program in programs]
    num_correct = sum(outcomes)

    result: dict[str, Any] = {
        "num_samples": n_samples,
        "num_correct": num_correct,
        "outcomes": outcomes,
    }
    for k in k_values:
        if k <= n_samples:
            result[f"pass@{k}"] = pass_at_k(n_samples, num_correct, k)
    return result


# --------------------------------------------------------------------------
# Bootstrap confidence intervals
# --------------------------------------------------------------------------


def bootstrap_ci(
    scores: Sequence[float],
    num_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int | None = None,
) -> dict[str, float]:
    """Percentile bootstrap confidence interval for the mean of ``scores``."""
    arr = np.asarray(scores, dtype=float)
    n = arr.shape[0]
    if n == 0:
        return {
            "mean": float("nan"),
            "low": float("nan"),
            "high": float("nan"),
            "confidence": confidence,
            "num_resamples": num_resamples,
        }

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(num_resamples, n))
    resample_means = arr[idx].mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    low, high = np.quantile(resample_means, [alpha, 1.0 - alpha])

    return {
        "mean": float(arr.mean()),
        "low": float(low),
        "high": float(high),
        "confidence": confidence,
        "num_resamples": num_resamples,
    }


def paired_bootstrap(
    scores_a: Sequence[float],
    scores_b: Sequence[float],
    num_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int | None = None,
) -> dict[str, float]:
    """Paired bootstrap comparison of two runs scored on the same,
    aligned examples. Returns the observed mean difference (a - b), a
    bootstrap confidence interval on that difference, and a two-sided
    empirical p-value for the null hypothesis that the difference is
    zero."""
    a = np.asarray(scores_a, dtype=float)
    b = np.asarray(scores_b, dtype=float)
    if a.shape != b.shape:
        raise ValueError(
            "paired_bootstrap requires two equal-length, example-aligned "
            "score arrays"
        )

    n = a.shape[0]
    if n == 0:
        return {
            "mean_diff": float("nan"),
            "low": float("nan"),
            "high": float("nan"),
            "confidence": confidence,
            "p_value": float("nan"),
            "num_resamples": num_resamples,
        }

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(num_resamples, n))
    diffs = a[idx].mean(axis=1) - b[idx].mean(axis=1)

    observed_diff = float(a.mean() - b.mean())
    alpha = (1.0 - confidence) / 2.0
    low, high = np.quantile(diffs, [alpha, 1.0 - alpha])
    p_value = float(2.0 * min((diffs >= 0).mean(), (diffs <= 0).mean()))
    p_value = min(p_value, 1.0)

    return {
        "mean_diff": observed_diff,
        "low": float(low),
        "high": float(high),
        "confidence": confidence,
        "p_value": p_value,
        "num_resamples": num_resamples,
    }


# --------------------------------------------------------------------------
# Per-subject breakdown
# --------------------------------------------------------------------------


def subject_breakdown(
    examples: Sequence[Mapping[str, Any]], scores: Sequence[float]
) -> dict[str, Any]:
    """Group ``scores`` by each example's ``subject`` field (when present)
    and report the macro-average across subjects."""
    per_subject: dict[str, list[float]] = {}
    for example, score in zip(examples, scores):
        subject = example.get("subject")
        if subject is None:
            continue
        per_subject.setdefault(str(subject), []).append(score)

    per_subject_mean = {
        subject: float(np.mean(values)) for subject, values in per_subject.items()
    }
    macro_average = (
        float(np.mean(list(per_subject_mean.values()))) if per_subject_mean else None
    )
    return {"per_subject": per_subject_mean, "macro_average": macro_average}


# --------------------------------------------------------------------------
# Result caching
# --------------------------------------------------------------------------


def compute_config_hash(config: Mapping[str, Any]) -> str:
    canonical = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


class ResultCache:
    """Disk-backed cache of scored results, keyed by (model, benchmark,
    config hash)."""

    def __init__(self, cache_dir: str | Path = ".benchmark_cache") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, model_name: str, benchmark_name: str, config: Mapping[str, Any]) -> Path:
        safe_model = _SAFE_NAME_RE.sub("_", model_name)
        safe_bench = _SAFE_NAME_RE.sub("_", benchmark_name)
        config_hash = compute_config_hash(config)
        return self.cache_dir / f"{safe_model}__{safe_bench}__{config_hash}.json"

    def get(
        self, model_name: str, benchmark_name: str, config: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        path = self._path(model_name, benchmark_name, config)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def set(
        self,
        model_name: str,
        benchmark_name: str,
        config: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        path = self._path(model_name, benchmark_name, config)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)


# --------------------------------------------------------------------------
# Benchmark configuration and orchestration
# --------------------------------------------------------------------------

TaskType = Literal["multiple_choice", "exact_match", "pass_at_k"]


@dataclass
class BenchmarkConfig:
    name: str
    task_type: TaskType
    examples: list[Mapping[str, Any]]
    format_example: FormatExample
    fewshot_pool: list[Mapping[str, Any]] = field(default_factory=list)
    num_shots: int = 0
    seed: int = 0
    # pass_at_k only
    n_samples: int = 1
    k_values: tuple[int, ...] = (1,)
    timeout: float = 5.0
    gen_kwargs: dict[str, Any] = field(default_factory=dict)

    def config_dict(self) -> dict[str, Any]:
        """The subset of configuration that determines the score, used as
        the cache key (deliberately excludes the example payloads and the
        formatting callable, which are not JSON-serialisable and do not
        need to be, since ``num_examples`` plus the benchmark name already
        distinguish different datasets under the same name)."""
        return {
            "task_type": self.task_type,
            "num_shots": self.num_shots,
            "seed": self.seed,
            "n_samples": self.n_samples,
            "k_values": list(self.k_values),
            "timeout": self.timeout,
            "gen_kwargs": self.gen_kwargs,
            "num_examples": len(self.examples),
        }


def _headline_scores_for_subjects(
    task_type: TaskType, per_example: Sequence[Mapping[str, Any]], k_values: Sequence[int]
) -> list[float]:
    """Pick the single per-example score array used for the subject
    breakdown, one per task type."""
    if task_type == "multiple_choice":
        return [ex["correct_normalized"] for ex in per_example]
    if task_type == "exact_match":
        return [ex["correct"] for ex in per_example]
    primary_k = f"pass@{k_values[0]}"
    return [ex.get(primary_k, 0.0) for ex in per_example]


def evaluate_benchmark(
    model: Model,
    model_name: str,
    config: BenchmarkConfig,
    cache: ResultCache | None = None,
) -> dict[str, Any]:
    """Run one benchmark against ``model`` and return its result dict,
    using ``cache`` (if given) to skip re-scoring an unchanged run."""
    config_dict = config.config_dict()
    if cache is not None:
        cached = cache.get(model_name, config.name, config_dict)
        if cached is not None:
            cached = dict(cached)
            cached["cached"] = True
            return cached

    sampler = FewShotSampler(config.fewshot_pool, config.num_shots, config.seed)
    per_example: list[dict[str, Any]] = []

    for example in config.examples:
        shots = sampler.sample(exclude=example)
        prompt = build_prompt(shots, example, config.format_example)

        if config.task_type == "multiple_choice":
            per_example.append(score_multiple_choice(model, example, prompt))
        elif config.task_type == "exact_match":
            per_example.append(
                score_exact_match(model, example, prompt, **config.gen_kwargs)
            )
        elif config.task_type == "pass_at_k":
            per_example.append(
                score_pass_at_k(
                    model,
                    example,
                    prompt,
                    config.n_samples,
                    config.k_values,
                    config.timeout,
                    **config.gen_kwargs,
                )
            )
        else:
            raise ValueError(f"unknown task_type: {config.task_type!r}")

    metrics, cis = _summarize_metrics(config, per_example)
    subject_scores = _headline_scores_for_subjects(
        config.task_type, per_example, config.k_values
    )
    subj = subject_breakdown(config.examples, subject_scores)
    headline = subj["macro_average"]

    result: dict[str, Any] = {
        "benchmark": config.name,
        "task_type": config.task_type,
        "num_examples": len(config.examples),
        "metrics": metrics,
        "confidence_intervals": cis,
        "subject_breakdown": subj if subj["per_subject"] else None,
        "headline_macro_average": headline,
        "per_example": per_example,
        "config_hash": compute_config_hash(config_dict),
        "cached": False,
    }

    if cache is not None:
        cache.set(model_name, config.name, config_dict, result)
    return result


def _summarize_metrics(
    config: BenchmarkConfig, per_example: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    metrics: dict[str, float] = {}
    cis: dict[str, dict[str, float]] = {}

    if config.task_type == "multiple_choice":
        for key, metric_name in (
            ("correct_normalized", "accuracy_normalized"),
            ("correct_unnormalized", "accuracy_unnormalized"),
        ):
            scores = [ex[key] for ex in per_example]
            metrics[metric_name] = float(np.mean(scores)) if scores else float("nan")
            cis[metric_name] = bootstrap_ci(scores, seed=config.seed)

    elif config.task_type == "exact_match":
        scores = [ex["correct"] for ex in per_example]
        metrics["exact_match"] = float(np.mean(scores)) if scores else float("nan")
        cis["exact_match"] = bootstrap_ci(scores, seed=config.seed)

    elif config.task_type == "pass_at_k":
        for k in config.k_values:
            key = f"pass@{k}"
            scores = [ex[key] for ex in per_example if key in ex]
            if scores:
                metrics[key] = float(np.mean(scores))
                cis[key] = bootstrap_ci(scores, seed=config.seed)

    return metrics, cis


def run_benchmarks(
    model: Model,
    model_name: str,
    configs: Sequence[BenchmarkConfig],
    output_dir: str | Path = ".",
    cache_dir: str | Path | None = ".benchmark_cache",
) -> dict[str, Any]:
    """Score every config in ``configs`` against ``model``, then write out
    ``results.json`` and ``results.md`` under ``output_dir``. Returns the
    same results dict that gets serialised to JSON."""
    cache = ResultCache(cache_dir) if cache_dir is not None else None

    results: dict[str, Any] = {}
    for config in configs:
        results[config.name] = evaluate_benchmark(model, model_name, config, cache=cache)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    write_results_json(output_path / "results.json", results)
    write_markdown_table(output_path / "results.md", results)
    return results


# --------------------------------------------------------------------------
# Output writers
# --------------------------------------------------------------------------


def write_results_json(path: str | Path, results: Mapping[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, sort_keys=True)


def write_markdown_table(path: str | Path, results: Mapping[str, Any]) -> None:
    lines = [
        "| Benchmark | Metric | Score | 95% CI | Macro Avg (subjects) |",
        "|---|---|---|---|---|",
    ]
    for name, result in results.items():
        metrics: Mapping[str, float] = result.get("metrics", {})
        cis: Mapping[str, Mapping[str, float]] = result.get("confidence_intervals", {})
        headline = result.get("headline_macro_average")
        headline_str = f"{headline:.4f}" if headline is not None else "-"

        if not metrics:
            lines.append(f"| {name} | - | - | - | {headline_str} |")
            continue

        for metric_name, value in metrics.items():
            ci = cis.get(metric_name)
            ci_str = f"[{ci['low']:.4f}, {ci['high']:.4f}]" if ci else "-"
            lines.append(
                f"| {name} | {metric_name} | {value:.4f} | {ci_str} | {headline_str} |"
            )

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
