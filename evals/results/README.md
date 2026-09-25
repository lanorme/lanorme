# Release audit results

This directory holds one JSON file per release, named `v<version>.json`,
written by [`../audit.py`](../audit.py). Each file is committed as the audit
trail for that release: a durable record of how LaNorme's heuristic rules scored
against their labelled corpora at the moment of cutting the version. The
`git_commit` and `git_dirty` fields pin the exact dataset and code that produced
the numbers.

## Schema

- `metadata`: the version and hardware stamp. It records `audited_version`,
  `lanorme_version`, `git_commit`, `git_dirty`, `python_version`, `platform`,
  `processor` and `timestamp_utc`.
- `corpus_problems`: every problem `validate_corpora.py` found (an unlabelled
  file or comment, a label off its site, missing provenance, a misplaced file).
  Empty on a passing audit.
- `accuracy`: a list with one entry per scorer. A scored entry carries `rule`,
  `corpus`, `split` (`hashed` or `too_small`), the combined `tp`, `fp`, `fn`,
  `tn`, `precision`, `recall` and `f1`, a `dev` and a `holdout` block with the
  same counts and ratios (`holdout` is `null` when the corpus is too small to
  split), a `holdout_generated` block when the corpus has generated cases, and
  `gap`, dev minus holdout per ratio. A ratio with no denominator is `null`. A
  scorer whose corpus has gone stale records `rule` and an `error` message
  instead of the numbers.
- `gate`: `null`, or, when the audit ran with `--gate`, the `baseline` it
  compared against, the `tolerance`, the holdout `regressions` it found and the
  rules it `skipped` for lack of a holdout baseline.
- `performance`: a map from corpus name to its end-to-end timing, or an empty
  map when the run used `--no-perf`. A corpus that could not be downloaded is
  recorded as skipped rather than failing the audit.

Files written before the dev and holdout split carry only the combined numbers,
so the gate skips every rule against them.

## Reading the numbers

The `accuracy` block is deterministic: the same code over the same labelled
corpora yields the same precision, recall and F1 on any machine, so these
numbers are directly comparable across releases. The holdout numbers are the
ones a change may not tune against, so they are the ones the gate holds; a large
positive `gap` means a rule does better on the data it was tuned on than on
data it was not. The `performance` block is machine-dependent, so always read it
against the hardware stamp in `metadata` (`platform`, `processor`,
`python_version`) before comparing timings.
