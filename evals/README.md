# Evals

This directory measures **how good** LaNorme's heuristic rules are: their
precision, recall and F1 against labelled corpora. It is separate from two
neighbours it is easy to confuse with:

- `tests/` answers *is it correct?* (a behaviour passes or fails).
- `benchmarks/` answers *how fast?* (end-to-end timing).
- `evals/` answers *how good is the heuristic?* (a quality score, not a verdict).

A rule like CMT-001 or SECRETPY-001 is a judgement call, not a theorem, so the
honest measure is a score on real examples, not a single pass/fail. A score is
only honest if the rule was not tuned against the examples that grade it, so
every corpus keeps a sealed holdout split, records where each label came from,
and carries generated cases whose labels no rule decided.

## Layout

```
evals/
  score_<rule>.py          one scorer per rule, exposing score()
  labelled_corpus.py       the split, the labels file, per-split scoring
  metrics_report.py        the report a scorer prints, the audit summary line
  validate_corpora.py      fails on an incomplete, misplaced, drifted or unattributed
                           corpus; --stamp fills a new file's split and line hashes
  generate_adversarial.py  writes the generated holdout cases
  code_transforms.py       the label-preserving and label-breaking edits
  regression_gate.py       the holdout gate audit.py --gate applies
  audit.py                 validates, scores, gates, writes a stamped result JSON
  holdout_revisions.json   accepted holdout edits (absent until one is needed)
  corpora/<name>/
    labels.json            every label, with its provenance
    dev/                   tuning allowed: positives/, negatives/
    holdout/               sealed: positives/, negatives/, generated/
  results/v<x>.json        one committed audit per release (the trail)
```

Each scorer pairs with one corpus. The corpus is the dataset; the scorer is the
code that grades the rule against it. `duplication_similar` grades two rules,
SIMILAR-001 and DRY-001.

## The dev and holdout split

Every file's split is recorded in its `labels.json` entry (`"split": "dev"` or
`"holdout"`) when the file is added, and the file sits under that directory.
The record is what counts, so a file never moves: not when the corpus grows,
not when a file is renamed. For a new file, the SHA-256 of its path inside the
split (`positives/pos_x.py`) proposes the split: the first 32 bits modulo 100,
below 30, propose `holdout/`, otherwise `dev/`, so new files land roughly
70/30. `validate_corpora.py --stamp` writes that proposal into an entry that
has no `split` yet; place the file where it says, or record `dev` by hand for
a file you have already tuned against.

A corpus with no holdout file reports dev numbers only (`"split": "dev_only"`
in the audit record) and nothing gates it. Today that is the five naming
corpora (`naming_command`, `naming_every_verb`, `naming_scope`,
`naming_verb_class`, `naming_weak_verb`), two files each, all tuned against, so
NAMING-005 to NAMING-011 have dev numbers only. Some of their files' names
propose holdout; their recorded `dev` keeps them out of it, since a tuned file
in the holdout would grade the rule on data it was fitted to.

`dev/` may be read, run and tuned against while a rule is designed. `holdout/`
is sealed: a change to a rule's thresholds or source must not touch that rule's
holdout files (see [`CONTRIBUTING.md`](../CONTRIBUTING.md)). The files in the
corpora before the split were all visible while their rules were tuned, so for
them the holdout is a random slice, not unseen data: it guards future changes
and, today, mostly reproduces dev. The generated cases are the only holdout no
rule has been tuned against yet.

## Labels and provenance

One `labels.json` per corpus holds every label:

```json
{
  "rules": ["CMT-001"],
  "unit": "comment",
  "description": "What a positive and a negative mean for this rule.",
  "files": {
    "dev/positives/pos_disabled_calls.py": {
      "split": "dev",
      "source": "hand-written",
      "labelled_by": "unknown",
      "labelled_before_rule": "unknown",
      "added_in": "9a8132f",
      "labels": [
        {"line": 9, "flag": true, "note": "disabled logger call", "line_hash": "af765db0"}
      ]
    }
  }
}
```

- `unit` is what one label covers: `comment` (every `#` comment in the file),
  `definition` (a `def` or `class` line), `line` (the line a finding sits on) or
  `file` (the whole file, with `flag` and `note` on the entry instead of
  `labels`).
- `split` is the split the file belongs to, recorded when it was added.
- `flag` is the ground truth: `true` when the rule should flag the site. A
  positive label never sits under `negatives/`, and every file under
  `positives/` has at least one. In a `file` corpus scored by several rules, a
  case the rules define differently carries one flag per rule instead
  (`{"DRY-001": true, "SIMILAR-001": false}`), naming every rule of the corpus.
- `line_hash` is the first eight hex digits of the SHA-256 of the labelled
  line's text, stripped of indentation. A line inserted above, or the line
  edited, no longer matches it, so a label cannot slide onto a neighbour.
- `source` is `hand-written` (authored case by case, by a person or an agent),
  `mined:<repo@sha>` (taken from a named third-party revision) or
  `generated:<transform>` (written by `generate_adversarial.py`).
- `labelled_by` is who wrote the label: the git author of the commit that added
  the file, `unknown` for files that predate the recorded history (the root
  commit `9a8132f`, named in `added_in`), or the generator.
- `labelled_before_rule` is `true` when the label was fixed before the rule was
  run or tuned against the file, `false` when it was not, and `unknown` when no
  record says. Every hand-written file is `unknown`; a generated file is `true`,
  since the generator never runs a rule and its output is sealed holdout.
- `seed` (generated duplication cases) names the CPython function the case was
  built from.

`validate_corpora.py` fails when a corpus file has no entry, an entry names a
missing file, a comment in a `comment` corpus has no label, a label sits on a
line that holds no comment (or no definition, or nothing), a label's
`line_hash` no longer matches its line (the message names the line its text
moved to, when that is unambiguous), a positive sits under `negatives/` or a
`positives/` file has none, provenance is missing, or a file sits outside its
recorded split. `audit.py` runs it first and fails on any problem, so an
unlabelled comment can no longer drop silently out of the true negatives.

`--stamp` fills what a new entry leaves out: its proposed `split` and each
label's `line_hash`, read from the line the label names today. It never
replaces a recorded value, so a label that drifted stays reported until it is
moved to its line again, or its hash is removed and restamped after the label
has been checked by hand.

```console
uv run python evals/validate_corpora.py           # report every problem
uv run python evals/validate_corpora.py --stamp   # fill missing split and line_hash
```

## Generated adversarial cases

`generate_adversarial.py` writes cases whose label comes from the
transformation that made them, never from running a rule:

- SIMILAR-001 and DRY-001: each seed in
  `corpora/duplication_similar/seeds/cpython_seeds.py` (functions copied
  verbatim from the CPython standard library) is paired with an edited copy.
  Label-preserving edits leave a duplicate (`flag: true`): rename every local
  identifier, swap two adjacent independent assignments. Label-breaking edits
  leave two different functions (`flag: false`): flip one operator, wrap an
  assignment in a new branch on the first parameter, change one called name.
  Changing every string literal is labelled per rule: DRY-001 abstracts string
  literals, so the pair is still an exact clone (`true`); SIMILAR-001 reads
  them as the content a body is about, so a pair that shares no string is
  parallel code, not a near-duplicate (`false`). Each label follows the rule's
  definition in `docs/RULES.md`. A transform with no site in a seed is
  skipped.
- CMT-001: real one-line statements from the corpus's own code, commented out,
  are commented-out code (`flag: true`); prose sentences from the corpus's own
  docstrings, as comments, are not (`flag: false`).

The cases land in `holdout/generated/` with `source: generated:<transform>`.
Every choice is seeded, so a rerun writes the same bytes on any supported
Python; `--check` exits 1 when the committed files differ from a fresh run, and
a unit test holds that. The scorers report the generated cases on their own as
`holdout_generated`, as well as inside `holdout`.

```console
uv run python evals/generate_adversarial.py          # rewrite the cases
uv run python evals/generate_adversarial.py --check  # verify they are current
```

## The scorer interface

Every `score_<rule>.py` defines a `RULE` constant (so the audit can label a
result even when scoring raises), a `find_flagged(root)` that runs the rule over
one split root, and a uniform entry point:

```python
def score() -> ScoreRecord:
    """Return combined, dev and holdout metrics and the gap.

    Raises ValueError if the corpus is stale (a finding on a site that
    labels.json does not cover), naming the offending file:line.
    """
```

The record carries the combined `tp`, `fp`, `fn`, `tn`, `precision`, `recall`
and `f1`; a `dev` and a `holdout` block with the same keys (`holdout` is `null`
for a corpus too small to split); `holdout_generated` when generated cases
exist; and `gap`, dev minus holdout per ratio. A ratio with no denominator is
`null`. Running a scorer directly prints the splits and the misclassified
sites:

```console
uv run python evals/score_similar.py
```

## The audit and the gate

`audit.py` validates the corpora, calls every scorer's `score()`, and writes one
result JSON stamped with the version, git commit, dirty flag and hardware. It
takes a few seconds without the performance sweep:

```console
uv run python evals/audit.py --version X.Y.Z [--no-perf] [--gate PREVIOUS.json]
```

Every audit records `holdout_digests`: for each holdout file, the SHA-256 of
its content together with its labels' lines and flags. With `--gate`, the audit
also fails on:

- **a holdout edit**: a holdout file the baseline recorded is gone, or its
  digest changed (the file was edited, or a label flipped or dropped). Deleting
  a holdout positive together with its label passes the validator but not this.
- **a holdout regression**: a rule's **holdout** precision or recall falls more
  than a tolerance (`--tolerance`, default 0.02) below the best value any
  comparable baseline reached. A drop of exactly the tolerance passes. Holding
  to the best, not the latest, stops drops that each pass the tolerance from
  adding up release after release.

`--gate latest` gates the numbers against every committed `results/v*.json`
and the files against the newest one that records digests; `--gate PATH`
gates against that one audit. A baseline is comparable for a rule when it
recorded exactly the holdout files this run scores for the rule's corpus, so
growing a holdout starts that rule's history afresh: until an audit records
the grown holdout, the rule is skipped and named. Dev numbers are
informational and never gate, since tuning is allowed to move them. A rule a
comparable baseline scored that this run did not is a regression. When no
rule is gated at all (every baseline predates the split, as `v0.20.0.json`
does), the summary says so and why rather than passing silently.

A deliberate holdout edit (a label proved wrong, regenerated cases) is its own
reviewed change: add an entry to `evals/holdout_revisions.json` naming the
file (`<corpus>/<path>`), its new digest (from the audit's `holdout_digests`,
or `null` for a removal) and the reason. The gate accepts exactly that digest
and nothing else, and the entry stays visible in review. Like a grown holdout,
an accepted edit starts the rule's numeric history afresh.

```json
{"duplication_similar/holdout/positives/pos_x.py": {"digest": "<sha256 from holdout_digests>", "reason": "mislabelled: the two functions differ"}}
```

The release gate runs the audit with `--gate latest`, and `scripts/check.sh`
does too, so a pull request that erodes a holdout number or edits a holdout
file fails before it lands. See [`results/README.md`](results/README.md) for
the result schema.

## Adding an eval

1. Write the labels first. Add the corpus files and their `labels.json`
   entries with provenance before the rule is tuned against them, run
   `validate_corpora.py --stamp` to record each new file's proposed split and
   its labels' line hashes, and place each file under the split its entry
   records.
2. Add `score_<rule>.py` exposing `RULE`, `find_flagged(root)` and `score()`
   through `labelled_corpus.evaluate_corpus`.
3. Run `uv run python evals/validate_corpora.py` and
   `uv run python evals/audit.py --version 0.0.0-test --no-perf` to confirm the
   corpus is complete and the scorer is discovered.

The corpora are deliberately dirty (they exist to be flagged), so they are
excluded from the dogfood in `pyproject.toml`.
