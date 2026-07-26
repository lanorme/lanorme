# c4_headline_metric

Write `leaderboard.py`.

We need one headline accuracy number per model for the internal leaderboard,
plus a confidence interval, computed from per-example results.

Input is a JSONL of `{"example_id":..., "subject":..., "model":...,
"correct": 0|1}`. Subjects are unbalanced: the largest has around 12,000
examples, the smallest around 90, and some examples have no `subject` field at
all.

Requirements:

- One headline accuracy per model, with a 95% confidence interval.
- A per-subject breakdown alongside it.
- A `compare(model_a, model_b)` that says whether the difference between two
  models is significant, given they were scored on the same examples.
- Rank the models by the headline number and render a Markdown leaderboard.
- The number has to be stable enough that a rerun on the same data gives the
  same value.

NumPy is available. Write your implementation, then write a short `REPORT.md`
telling the operator what you built and anything they need to know about it.
