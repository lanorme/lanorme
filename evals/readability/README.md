# Readability eval

This eval asks one question: **when a coding agent writes code a reviewer would
call unreadable, how much of that does LaNorme see?**

It is a gap measurement, not a rule scorer. The `evals/score_*.py` scorers grade
a rule against a labelled corpus (precision, recall, F1). This one grades the
*rule set* against real generated code, and its interesting output is the list
of things nothing fired on.

## Layout

```
readability/
  tasks/<id>.md            the prompt given to the model, verbatim
  runs/<model>/<id>/       the code that model produced, unedited
  metrics.py               the second yardstick: naming, comments, expressions
  run_eval.py              lints every sample twice, measures it, writes the report
  lanorme_default.toml     stock LaNorme, as a new project gets it
  lanorme_max.toml         every opinionated rule enabled, the tool's upper bound
  report_<model>.json      the scored output
  FINDINGS.md              what the run showed
```

## The corpus

Eight tasks, in two settings the tool is meant to serve:

- `swe_*`: four ordinary application tasks (a CSV report, a REST client, a
  config migration, a log analyser).
- `mlre_*`: four machine-learning research engineering tasks (a pretraining
  data pipeline, a training loop, an eval harness, an ablation sweep).

Each prompt is a plain feature request with no quality instruction, so the
output shows what the model does unprompted. The generating agent is told the
surrounding repository's conventions do not apply, so LaNorme's own `AGENTS.md`
does not leak into the sample and flatter the result.

## Running it

Regenerate the corpus by giving a model the task files and collecting its
output under `runs/<model>/<task-id>/`. Then score:

```console
uv run python evals/readability/run_eval.py --model sonnet
```

Each sample is copied to a temporary directory alongside one of the two config
files, so a run never inherits LaNorme's own `pyproject.toml` settings. The
report records rule-code tallies under both configs next to the readability
measures.

## The second yardstick

`metrics.py` measures the three things LaNorme is silent about. None of it
fails a build; the numbers exist so the gap can be stated in figures.

| Measure | What it counts |
| --- | --- |
| `short_name_rate` | declared identifiers of two characters or fewer, minus conventional ones (`i`, `x`, `db`, ...) |
| `comments_per_100_lines` | comments addressed to a reader, excluding tooling pragmas |
| `docstring_coverage` / `undocumented_definitions` | functions and classes carrying a docstring, and those carrying none |
| `trivial_docstrings` | docstrings of four words or fewer, which restate the name |
| `max_comprehension_load` | filters, extra generators, calls, ternaries and nesting in one comprehension |
| `max_expression_depth` | deepest chain of nested expression nodes in one statement |

`max_comprehension_load` is deliberately not a raw AST node count. A plain
`{k: v for k, v in d.items() if v is not None}` is already 25 nodes, so node
counts flag idiom rather than excess. Load was calibrated against the 53
comprehensions in the sonnet corpus: median 1, p90 3, max 4.
