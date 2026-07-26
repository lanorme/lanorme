# mlre_03_eval_harness

Write `benchmark_eval.py`, a harness that scores model outputs on a set of
benchmarks.

Support three task shapes:

- Multiple choice, scored by length-normalised log-likelihood of each option
  and by unnormalised log-likelihood, reporting both.
- Exact match generation, with a normalisation chain (lowercase, strip
  articles, strip punctuation, collapse whitespace) applied before comparison.
- Pass@k for code tasks, using the unbiased estimator over n samples.

Also:

- Few-shot prompting with a fixed seed for exemplar selection, and a
  configurable number of shots.
- Bootstrap confidence intervals over per-example scores, plus a paired
  bootstrap for comparing two runs on the same examples.
- Per-subject breakdowns when the dataset has a `subject` field, with a
  macro-average across subjects as the headline number.
- Result caching keyed by (model, benchmark, config hash) so a rerun is cheap.
- Output a results JSON and a Markdown table.

Assume a `Model` protocol with `.loglikelihood(context, continuation)` and
`.generate(prompt, **kwargs)`. NumPy available.
