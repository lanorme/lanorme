# REPORT

## What this is

`leaderboard.py` turns a JSONL log of per-example results
(`{"example_id", "subject", "model", "correct"}`) into:

- one headline accuracy per model, with a 95% confidence interval
- a per-subject accuracy breakdown per model
- `compare(model_a, model_b, records)` -- a paired significance test between
  two models scored on the same examples
- a rendered Markdown leaderboard, ranked by the headline number

Everything is pure standard library plus NumPy (used for the array
reductions); no scipy, no pandas.

## The one decision that matters: what "headline" means

Subjects are badly unbalanced (~90 to ~12,000 examples). A simple pooled
accuracy over every example (a "micro" average) would be dominated almost
entirely by whichever subject has 12,000 examples -- a model could improve on
that one subject and barely move on everything else, and still look like it
got much better overall. That is usually not what you want out of a
leaderboard number.

So **the headline accuracy is the macro average**: the unweighted mean of
each model's own per-subject accuracy. Every subject counts the same
regardless of how many examples it has. The simple pooled accuracy is still
computed and shown in the leaderboard table as "Overall (micro)" for
context, and models are **ranked on the macro headline**, not the micro one.

If your use case actually wants the pooled/micro number to be the ranking
key (e.g. you deliberately want subject size to act as an importance
weight), swap which column `rank_models()` sorts on -- everything else keeps
working unchanged, since both numbers are computed regardless.

## Examples with no subject

Some rows have no `subject` field at all. Those examples:

- **are** included in the overall/micro accuracy (they are still valid
  scored examples),
- **are not** included in the macro/headline accuracy, because macro
  averaging is defined over subject groups and there's no group to put them
  in,
- **do** show up in the per-subject breakdown table as their own row,
  labelled `(no subject)`, with a `Counts toward headline: no` flag, so they
  are visible rather than silently dropped.

If a model has *zero* examples with a real subject, `score_model` (and
`score_all`) raise `ValueError` rather than silently returning a
meaningless headline -- that model's data needs a subject before it can be
ranked.

## Confidence intervals

All CIs are closed-form, not bootstrapped:

- **Per-subject and overall/micro CI**: Wilson score interval. It stays
  inside [0, 1] and stays sane for the small subject (~90 examples) and for
  accuracies near 0% or 100%, unlike the naive `p +/- z*sqrt(p(1-p)/n)`
  normal approximation.
- **Headline/macro CI**: the macro accuracy is a mean of K independent
  per-subject proportions, so its variance is the sum of the per-subject
  variances divided by K^2. Each per-subject variance uses an
  Agresti-Coull-style adjustment (`(k + z^2/2) / (n + z^2)` used as the
  plug-in proportion for the variance) rather than the raw `p(1-p)/n`, so a
  subject that happens to be 100% (or 0%) correct doesn't contribute a
  degenerate zero variance to the combined interval.

**Why no bootstrap**: the spec asks for a number that reruns to the same
value on the same data. A closed-form interval has nothing random in it, so
that's automatic -- there's no seed to manage, and no risk of a rerun
producing a slightly different CI because of a different RNG stream. If you
later want a resampling-based CI (e.g. to relax the independence assumption
between subjects, or if the normal-approximation machinery above ever looks
too optimistic for very small subjects), seed the RNG once with a fixed
constant and it'll be equally reproducible -- just a different, heavier
computation.

## `compare(model_a, model_b, records, alpha=0.05)`

Uses McNemar's test on the discordant pairs (examples where the two models
disagree), which is the standard paired test for "same items, two binary
classifiers, are they different" -- it's what applies when the models were
scored on the same examples, as the spec assumes. It:

- restricts to the intersection of `example_id`s the two models actually
  share (and reports `n_a_only_dropped` / `n_b_only_dropped` so you can see
  if the assumption that they share the same example set doesn't quite
  hold -- it doesn't raise on a mismatch, it just tells you),
- uses the **exact** binomial form of McNemar's test when there are <=200
  discordant pairs (small-sample exact, computed with `math.comb`, no
  approximation), and the continuity-corrected normal approximation above
  that threshold (fast and accurate at scale),
- returns a `ComparisonResult` with `p_value`, `significant` (at `alpha`),
  and a `.summary()` string for a quick human-readable read.

`compare` returns a result object rather than just a bool so the operator
can see the effect size (`diff`), which model was better, and the raw
discordant-pair counts, not just yes/no.

## Using it

```console
python3 leaderboard.py results.jsonl
python3 leaderboard.py results.jsonl --compare model_a model_b --alpha 0.05
```

or as a library:

```python
from leaderboard import load_records, score_all, render_markdown, compare

records = load_records("results.jsonl")
scores = score_all(records)
print(render_markdown(scores))

result = compare("model_a", "model_b", records)
print(result.summary())
```

## Things to know before relying on this

- **Independence assumption in the macro CI.** The macro-mean variance sums
  per-subject variances as if subjects were independent. That's true here
  (each subject is a disjoint set of examples), but it does *not* account
  for a model having systematically correlated errors across subjects (e.g.
  a formatting bug that fails every subject at once). The CI describes
  sampling noise from having a finite example set per subject, not model
  variance across runs/seeds.
- **`example_id` and `model` must be present on every row**; `correct` must
  be `0`, `1`, or a bool. Anything else raises `ValueError` naming the file
  and line number, rather than silently coercing bad data.
- **Ties in `compare`** (both models right, or both wrong, on an example)
  are correctly ignored -- McNemar's test only uses disagreements, which is
  what makes it more powerful than an unpaired test for this exact setup.
- **Large discordant-pair counts** can drive the normal-approximation
  p-value to `0.0` in float64 (it underflows rather than staying a tiny
  positive number). `ComparisonResult.summary()` renders that case as
  `p < 1e-300` rather than the misleading `p=0`.
