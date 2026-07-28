# Findings: what LaNorme sees in generated code

Run: `sonnet`, 8 tasks, 8 files, 3321 effective lines. Scored with
`run_eval.py`; raw numbers in `report_sonnet.json`.

## The headline

Across 3321 lines of unprompted model output, LaNorme emitted **95 findings
under the stock defaults**, and every one of them was about size, typing,
parameters or security:

| Rule | Findings | What it is about |
| --- | --- | --- |
| `TYPE-001` | 49 | typing |
| `COMPLEXITY-001` | 9 | size |
| `PARAM-001` | 9 | size |
| `SIZE-001` | 8 | size |
| `SIZE-002` | 7 | size |
| `TYPE-003` | 5 | typing |
| `TYPE-002` | 3 | typing |
| `NAMING-004` | 1 | naming (boolean prefix only) |
| `CRYPTO-001`, `IMPORT-001`, `SIZE-003`, `SECRETPY-001` | 1 each | security, structure, size |

Turning on every opinionated rule adds 82 `KWARG-001` and 3 `ATTR-001`, both
calling conventions. **Not one finding in either configuration is about whether
a human can read the code.**

Meanwhile the same corpus carries **180 undocumented functions and classes**,
at a median docstring coverage of **31 per cent**. `train.py` documents 8 per
cent of its definitions; `sweep.py` leaves 36 definitions with no docstring at
all. LaNorme has nothing to say about any of it, because it has no rule that
can.

> **Amended after the longitudinal eval.** That 31 per cent is substantially
> an artefact of the task format, not a general property of generated code.
> Every task in this corpus asks for a single 300 to 500 line file. When
> `evals/longitudinal/` asks the same model for a *package*, docstring
> coverage runs 52 to 69 per cent and rises with every turn. Module
> boundaries carry documentation pressure a monolithic script does not. The
> measurement stands; the generalisation "agents underdocument" does not.
> What the evidence supports is "agents underdocument long single files".

## The three complaints, tested

**"Not enough comments" reproduces, and it is the real gap.** Median
explanatory comment density is 6.2 per 100 lines, and `sweep.py` runs at 3.8.
Docstring coverage is the sharper signal: 180 undocumented definitions. This is
not merely unmeasured, it is structurally encouraged, see below.

**"Terrible variable names" reproduces, but narrowly.** Short-name rate is
0.0 to 5.4 per cent on seven files. The exception is the ML sweep at 9.8 per
cent, where mathematical shorthand leaks out of the fit and into the driver:
`a0`, `c0`, `p0`, `rc`, `r`, `a`, `b`, `c` as ordinary locals. The pattern is
real but it concentrates in numeric code rather than spreading everywhere.

**"List comprehensions that do hundreds of things" does not reproduce.** Across
53 comprehensions the load metric runs median 1, p90 3, max 4, against a budget
of 6. The heaviest in the whole corpus is
`[p for p in log_dir.iterdir() if p.is_file() and not p.name.startswith(".")]`.
Whatever produces comprehension soup, it is not a fresh file written from a
clear specification, which is what this corpus samples.

## Why the rule set cannot see it

Three structural reasons, each verifiable in the source.

### 1. Every comment rule subtracts

`CMT-001` removes commented-out code. `CMT-002` caps a comment at 120
characters and a block at **6 consecutive lines**. `CMT-005` removes comments
that restate the next line. `PROSE-001` and `PROSE-003` remove em dashes and
emoji. There is no rule anywhere in `src/lanorme/checks/` that requires a
comment or a docstring to exist.

The gradient this creates points one way. An agent optimising for a green run
learns that deleting a comment is always safe and writing one is a risk, and
that a seven-line explanation of a subtle algorithm is a `CMT-002` violation
while no explanation at all is clean. The tool that was meant to produce
readable code is, on this axis, pushing in the wrong direction.

### 2. Nothing scores an identifier

`grep` for identifier handling finds `domain_terms` (`TERM-NNN`), and it only
enforces a project's own supplied glossary of forbidden and canonical words. It
cannot say that `d`, `m`, `t` and `r` are bad names, only that `customer` was
spelled `client` when the glossary said otherwise. `NAMING-001..004` cover CRUD
prefixes on repository and service methods, HTTP verb agreement on endpoint
handlers, and boolean prefixes on predicates. None of them looks at whether a
name carries meaning, and `TERM` is in LaNorme's own `ignore` list.

### 3. The size rules measure lines, not density

`SIZE-002` counts a function's lines and `COMPLEXITY-001` counts its branches.
Neither notices a single statement doing a paragraph of work, and
`COMPLEXITY-001` deliberately does not count a comprehension's primary `for`
(documented in `RULES.md`). So the branching a dense expression hides is
partially discounted by design, and the density itself is not measured at all.

## The adversarial probe

`probe/probe_unreadable.py` in the scratch run is 29 effective lines of
deliberately unreadable code: functions named `proc`, `go` and `h`, parameters
`d`, `m`, `t`, `p`, `o`, docstrings reading `"""Process."""`, `"""Go."""` and
`"""H."""`, a four-key nested dict comprehension with a lambda sort, and no
explanatory comment anywhere. Its measured profile is a 66.7 per cent
short-name rate, zero comments and an expression depth of 9.

Under the stock defaults LaNorme reports **all 25 checks passed**. With every
opinionated rule enabled it reports three `KWARG-001` violations, about the
calling convention, and nothing else.

## What would close the gap

Four rules, in the order their evidence supports:

1. **A docstring requirement** on public functions, classes and modules
   above a size floor, plus a trivial-docstring check so `"""Go."""` does
   not satisfy it. This is the largest measured gap, 180 instances.
   *Shipped as `CMT-006` / `CMT-007`. The codes `CMT-003` / `CMT-004`
   proposed here were already retired under `PROSE-001` / `PROSE-003`, so
   reusing them would have been a silent breaking change. On the
   longitudinal corpus the pair fires seven times in 5000 lines: a floor
   against the bad case, not a detector of the common one.*
2. **Relax `CMT-002`'s block cap.** A 6-line limit on consecutive comments
   penalises exactly the explanation a hard piece of code needs. Either raise
   it substantially or exempt blocks that sit above a definition.
3. **`NAMING-005`, an identifier-length floor** for declared names outside a
   conventional allowlist, scoped so numeric code can opt out. Default-off and
   opinionated, matching the house rule for judgement calls.
4. **`COMPLEXITY-002`, expression density**: comprehension load, or expression
   depth, per statement. Lowest priority, since this corpus shows the failure
   mode is rare in greenfield code. Worth corpus work on edited code before
   committing to a threshold.

Items 1 and 2 are the ones this run actually justifies. Items 3 and 4 need a
wider corpus first, per the corpus discipline in `CONTRIBUTING.md`.

## Limits of this run

One model, one sample per task, greenfield files written from clear
specifications. It does not sample the case most likely to produce the worst
code: an agent editing an existing file under time pressure, patching around
code it did not write. A second round should cover edits and a second model
before any threshold here is treated as calibrated.
