# Findings: what decays over a longer task

Run: `sonnet`, 3 projects x 3 turns, every turn extending the previous turn's
code. Nine snapshots, 267 to 1130 effective lines. Table from `track.py`; raw
numbers in `report.json`.

## The table

| project | turn | files | lines | doc% | cmt/100 | short% | findings/kloc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| featurestore | 1 | 6 | 267 | 52% | 3.4 | 3.1% | 14.98 |
| featurestore | 2 | 8 | 536 | 56% | 3.4 | 2.3% | 16.79 |
| featurestore | 3 | 10 | 928 | 58% | 2.7 | 2.1% | 18.32 |
| ledger | 1 | 5 | 334 | 62% | 1.5 | 0.0% | 2.99 |
| ledger | 2 | 8 | 677 | 64% | 2.2 | 1.1% | 5.91 |
| ledger | 3 | 10 | 1032 | 68% | 2.1 | 0.7% | 8.72 |
| scheduler | 1 | 6 | 471 | 52% | 2.3 | 2.8% | 6.37 |
| scheduler | 2 | 7 | 675 | 59% | 2.5 | 2.0% | 7.41 |
| scheduler | 3 | 12 | 1130 | 69% | 3.2 | 1.5% | 11.50 |

Findings are per 1000 effective lines throughout. A raw count rises because the
codebase grew, so an unnormalised table would show decay for a project whose
quality never moved.

## Two results, both unanimous, and neither the one this eval was built to find

**Readability improves under extension.** Docstring coverage rose in all three
projects, by 6, 6 and 17 points, while each codebase tripled or quadrupled.
Not one project ended less documented than it started.

**Structure decays under extension.** Normalised finding density rose in all
three: 1.22x, 2.92x, 1.81x. The composition is the point. Across all nine
snapshots:

| rule | count | what it is about |
| --- | --- | --- |
| `PARAM-001` | 18 | signatures growing |
| `SIZE-003` | 12 | classes growing |
| `TYPE-001` | 10 | placeholder types |
| `COMPLEXITY-001` | 7 | branching |
| `SIZE-002` | 7 | functions growing |
| `SIZE-001` | 5 | files growing |
| `CMT-006` | 5 | missing docstrings |
| `SUPPRESS-001` | 3 | one pre-existing `# noqa` |
| `CMT-007` | 2 | vacuous docstrings |
| `NAMING-004` | 2 | boolean prefix |
| `TYPE-003` | 1 | bare container |

Forty-nine of the 72 findings are size and complexity. `PARAM-001` alone
sextupled in `featurestore` (1, 3, 6). What degrades is accretion: a signature
gaining a parameter per turn, a class gaining methods, a file outgrowing its
limit. That is exactly what you would expect to be lost between turns.
Documenting the function in front of you is local and the model does it
reliably. Noticing that `Ledger` has quietly reached fifteen methods requires
holding the whole design in mind, which is the thing an agent does not carry
across a turn boundary.

## What this says about the rules added alongside it

Across roughly 5000 lines of longitudinal code, the four rules added in this
work produced **seven findings**: five `CMT-006`, two `CMT-007`, and zero
`NAMING-005`. The three `SUPPRESS-001` hits are one pre-existing `# noqa` in
the scheduler counted against a zero budget, not decay.

`NAMING-005` did not fire once, on any snapshot, of any project. It found nine
instances in the single-file readability corpus and none here. Short-name rate
*fell* in two of three projects as they grew.

So on the question this eval was built to answer, the honest answer is that the
rules added today contribute almost nothing. What decays over a long task is
caught by `SIZE-*`, `PARAM-001` and `COMPLEXITY-001`, which LaNorme has had all
along. The case for `CMT-006/007` and `NAMING-005` remains what it was when
they shipped: a floor against the bad case, not a detector of the common one.
`CMT-007`'s two findings are both true positives, and one of them is worth
having (`ResourcePool.release`, documented in the commit that recorded it), but
two findings in 5000 lines is a guard rail, not a headline.

## The correction this forces on the readability eval

`evals/readability/FINDINGS.md` reports 31% median docstring coverage and 180
undocumented definitions, framed as a finding about generated code. Every
project here sits between 52% and 69%, rising with every turn.

The difference is the task format. The readability corpus asked for a single
300 to 500 line file; this one asks for a package. The same model documents
roughly twice as much of a package. Module boundaries appear to carry
documentation pressure that a monolithic script does not: an `errors.py` or an
`__init__.py` gets a docstring almost automatically, where the twentieth helper
buried in a long script does not.

The measurement was right and the generalisation was too broad. "Agents
underdocument" is not supported. "Agents underdocument long single files" is.
That correction is recorded in the readability findings too.

## What did not reproduce

Comment density has no consistent direction: 3.4 to 2.7 in `featurestore`, 1.5
to 2.1 in `ledger`, 2.3 to 3.2 in `scheduler`. The turn-3 dip that looked like a
trend after one project is noise.

Short-name rate fell in two projects and rose 0.7 points in the third from a
floor of zero. No trend.

## Limits

Three projects, one model, one sample per cell, three turns. Three turns is
short: real decay stories run to dozens of turns, several contributors, and
code the agent never wrote. Between-project variance is also large enough to
matter, and it moves in a way worth naming: `ledger` started cleanest (2.99)
and decayed fastest (2.92x), `featurestore` started worst (14.98) and moved
least (1.22x), and both ended in the 8 to 18 band. That pattern looks more like
regression toward a common ceiling than a property of either domain, so the
per-project decay *rates* here should not be quoted. The direction is solid
because it is unanimous; the magnitude is not.
