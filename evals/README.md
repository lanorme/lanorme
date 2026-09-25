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
  validate_corpora.py      fails on an incomplete, misplaced or unattributed corpus
  generate_adversarial.py  writes the generated holdout cases
  code_transforms.py       the label-preserving and label-breaking edits
  regression_gate.py       the holdout gate audit.py --gate applies
  audit.py                 validates, scores, gates, writes a stamped result JSON
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

A hand-labelled file's split is fixed by the SHA-256 of its path inside the
split (`positives/pos_x.py`): the first 32 bits modulo 100, below 30, put it in
`holdout/`, otherwise in `dev/`. The rule depends on the file name alone, so it
is reproducible, roughly 70/30, and adding a file never moves another. A corpus
whose hash split would leave fewer than three files on either side is too small
to split: every file stays in `dev/` and `labels.json` records
`"split": "too_small"`. Today that is the five naming corpora (`naming_command`,
`naming_every_verb`, `naming_scope`, `naming_verb_class`, `naming_weak_verb`),
two files each, so NAMING-005 to NAMING-011 have dev numbers only and no gate.

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
  "split": "hashed",
  "files": {
    "dev/positives/pos_disabled_imports.py": {
      "source": "hand-written",
      "labelled_by": "unknown",
      "labelled_before_rule": "unknown",
      "added_in": "9a8132f",
      "labels": [
        {"line": 8, "flag": true, "note": "disabled import statement"}
      ]
    }
  }
}
```

- `unit` is what one label covers: `comment` (every `#` comment in the file),
  `definition` (a `def` or `class` line), `line` (the line a finding sits on) or
  `file` (the whole file, with `flag` and `note` on the entry instead of
  `labels`).
- `flag` is the ground truth: `true` when the rule should flag the site. In a
  `file` corpus scored by several rules, a case the rules define differently
  carries one flag per rule instead (`{"DRY-001": true, "SIMILAR-001": false}`),
  naming every rule of the corpus.
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
line that holds no comment (or no definition, or nothing), provenance is
missing, or a file sits in the wrong split. `audit.py` runs it first and fails
on any problem, so an unlabelled comment can no longer drop silently out of
the true negatives.

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

With `--gate`, the audit also fails when any rule's **holdout** precision or
recall falls below the previous result minus a tolerance (`--tolerance`,
default 0.02), and lists the rules. A drop of exactly the tolerance passes. Dev
numbers are informational and never gate, since tuning is allowed to move them.
A rule the baseline has no holdout numbers for is skipped and named; a rule the
baseline scored that this run did not is a regression. The release gate runs
the audit with `--gate` against the latest committed result, and
`scripts/check.sh` does too, so a pull request that erodes a holdout number
fails before it lands. See [`results/README.md`](results/README.md) for the
result schema.

## Adding an eval

1. Write the labels first. Add the corpus files and a `labels.json` with
   provenance before the rule is tuned against them, and place each
   hand-labelled file where its name hashes (`validate_corpora.py` names the
   right split for a misplaced file).
2. Add `score_<rule>.py` exposing `RULE`, `find_flagged(root)` and `score()`
   through `labelled_corpus.evaluate_corpus`.
3. Run `uv run python evals/validate_corpora.py` and
   `uv run python evals/audit.py --version 0.0.0-test --no-perf` to confirm the
   corpus is complete and the scorer is discovered.

The corpora are deliberately dirty (they exist to be flagged), so they are
excluded from the dogfood in `pyproject.toml`.
