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
  `corpus`, `split` (`dev_and_holdout`, or `dev_only` for a corpus with no
  holdout file), the combined `tp`, `fp`, `fn`,
  `tn`, `precision`, `recall` and `f1`, a `dev` and a `holdout` block with the
  same counts and ratios (`holdout` is `null` when the corpus has no holdout
  file), a `holdout_generated` block when the corpus has generated cases, and
  `gap`, dev minus holdout per ratio. A ratio with no denominator is `null`. A
  scorer whose corpus has gone stale records `rule` and an `error` message
  instead of the numbers.
- `holdout_digests`: a map from corpus name to `{path inside the corpus:
  digest}` for every holdout file, the digest being the SHA-256 of the file's
  content and its labels' lines and flags. The next gate holds these files to
  it.
- `gate`: `null`, or, when the audit ran with `--gate`, the `baseline` it
  compared against, the `tolerance`, the rules it `gated`, the holdout
  `regressions` and `holdout_changes` (files removed or changed) it found, the
  rules it `skipped` for lack of a comparable baseline, and `notes` (for
  example that no rule was gated, and why).
- `performance`: a map from corpus name to its end-to-end timing, or an empty
  map when the run used `--no-perf`. A corpus that could not be downloaded is
  recorded as skipped rather than failing the audit.

Files written before the dev and holdout split carry only the combined numbers
and no digests, so the gate can hold nothing to them and its summary says so.

## Reading the numbers

The `accuracy` block is deterministic: the same code over the same labelled
corpora yields the same precision, recall and F1 on any machine, so these
numbers are directly comparable across releases. The holdout numbers are the
ones a change may not tune against, so they are the ones the gate holds; a large
positive `gap` means a rule does better on the data it was tuned on than on
data it was not. The `performance` block is machine-dependent, so always read it
against the hardware stamp in `metadata` (`platform`, `processor`,
`python_version`) before comparing timings.
