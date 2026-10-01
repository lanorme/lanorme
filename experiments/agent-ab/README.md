# Agent A/B: results

Six Opus 5.5 agents built the same service in three stages: three told to use
LaNorme (version 0.21.0, configured by the agent itself), three not. `SPEC.md`
has the design; this page has what came out. Everything here can be
regenerated from `runs/` with the scripts in `harness/`.

## Headline

- **Structure is where LaNorme shows.** Every LaNorme run split the code into
  modules (15 to 31 app files at stage 3); every control run stayed at 8 flat
  files and grew one large `create_app` closure holding every route (113 to 159
  lines). The longest LaNorme function at stage 3 was 46 lines.
- **Third-party tools agree, without being told what LaNorme wants.** On every
  stage the LaNorme arm has lower mean and max cyclomatic complexity, no
  function graded C or worse (control: up to 3), a maintainability index 11 to
  13 points higher, a half to two thirds of the ruff findings once line length
  is set aside, and fewer mypy errors.
- **Contract correctness is a draw.** All 18 snapshots pass every hidden
  acceptance test (31, 59 and 82 at stages 1, 2 and 3).
- **The blind judge preferred LaNorme 5 to 4**, losing two of three at stages 1
  and 2 and winning all three at stage 3. Design scored higher for LaNorme in
  all 9 pairs and readability in 7 (two ties); correctness averaged 7.0 for
  both.
- **It costs more.** LaNorme stages took about 1.6 times the turns and 1.7
  times the cost (USD 6.6 against USD 4.0 per run across all three stages).

## Where the judge split the arms

The judge cited edge cases beyond the HTTP contract, so these are what decided
correctness:

| Edge case | Control | LaNorme |
| --- | --- | --- |
| Tool-call limit also counts calls made by the deepagents subagent (`task`) | runs 2 and 3 | run 1 |
| `/chat/stream` streams incrementally, redacting PII split across chunks | none (all three buffer the whole turn, then re-split the reply) | all three |

The subagent loophole is a semantic bug no linter sees, and it was set in
stage 1 and carried forward in each run; it was the deciding point in all four
control wins. Real streaming is the hardest requirement in stage 3; every
LaNorme run solved it with a separate, tested redaction component, and every
control run sidestepped it.

## Third-party measurements

Means per arm, control / LaNorme. App code only unless stated.

| Metric | Stage 1 | Stage 2 | Stage 3 |
| --- | --- | --- | --- |
| Source lines (SLOC) | 274 / 288 | 421 / 424 | 562 / 701 |
| Files | 6 / 13.3 | 7 / 18 | 8 / 21.3 |
| Mean cyclomatic complexity | 3.2 / 2.5 | 2.5 / 2.0 | 2.3 / 2.1 |
| Max cyclomatic complexity | 10.7 / 6 | 10.7 / 6 | 13 / 8.7 |
| Functions graded C or worse | 0.7 / 0 | 1.3 / 0 | 2 / 0 |
| Maintainability index | 73.9 / 86.7 | 72.6 / 85.4 | 72.9 / 83.5 |
| Mean function length | 10.6 / 8.4 | 8.8 / 6.9 | 9.3 / 7.3 |
| Longest function | 37.7 / 23.7 | 74.7 / 27.3 | 141.3 / 36.7 |
| ruff `ALL` findings per 100 SLOC | 16.8 / 12.6 | 19.1 / 12.8 | 19.5 / 13.5 |
| ruff findings excluding line length | 35.7 / 23.3 | 59.3 / 29.7 | 80.7 / 40.3 |
| mypy errors (default / strict) | 3 / 0, 7 / 2 | 4.3 / 0.7, 9.3 / 4 | 5 / 4.3, 11.3 / 8 |
| bandit findings | 0.7 / 0.7 | 0.7 / 0.7 | 0.7 / 0.7 |
| vulture unused code (confidence 60) | 3 / 2.7 | 5.3 / 2.3 | 4.7 / 3 |
| Own tests | 75 / 80 | 137 / 138 | 193 / 228 |
| Own test line coverage, % | 98.2 / 99.6 | 98.1 / 99.6 | 98.4 / 99.6 |
| Agent turns | 19.7 / 35.7 | 17.7 / 23.3 | 19.7 / 34.7 |
| Cost, USD | 1.2 / 2.3 | 1.1 / 1.5 | 1.6 / 2.8 |
| Wall time, minutes | 5.7 / 6.6 | 5.0 / 5.2 | 7.6 / 11.8 |

Tool versions: radon 6.0.1, ruff 0.15.8, mypy 1.19.1, bandit 1.9.4, pylint
4.1.1, vulture 2.16. pylint found no cross-file duplication in any snapshot
(it does not see copies inside one file). The LaNorme arm has more ruff E501
(line length, 88 columns by default) findings: no run configured a line length,
LaNorme does not check it, and that arm wrote longer lines, largely keyword
arguments at call sites. That is why the table also shows ruff without it. Per
snapshot figures are in `results/metrics.json`.

## Blind judge

A fresh Opus session per pair, read-only tools, both codebases anonymised and
stripped of every LaNorme trace, letters shuffled. Scores are correctness,
design, readability, maintainability (1 to 10).

| Pair | Winner | LaNorme | Control | Confidence |
| --- | --- | --- | --- | --- |
| stage 1, run 1 | LaNorme | 8 8 8 8 | 5 6 7 6 | medium |
| stage 1, run 2 | control | 6 7 7 7 | 8 6 7 7 | medium |
| stage 1, run 3 | control | 6 8 8 7 | 8 6 6 7 | medium |
| stage 2, run 1 | LaNorme | 7 8 8 7 | 6 6 7 7 | medium |
| stage 2, run 2 | control | 6 8 8 8 | 8 7 7 7 | low |
| stage 2, run 3 | control | 6 8 8 7 | 8 6 6 7 | medium |
| stage 3, run 1 | LaNorme | 8 8 7 8 | 7 6 7 7 | medium |
| stage 3, run 2 | LaNorme | 8 8 8 8 | 6 6 7 7 | medium |
| stage 3, run 3 | LaNorme | 8 8 8 8 | 7 5 6 6 | medium |
| **Mean** | **5 to 4** | **7.0 7.9 7.8 7.6** | **7.0 6.0 6.7 6.8** | |

Each verdict, with its cited bugs and summary, is in `results/judge/`;
`results/judge/key.json` maps the letters back to arms.

## How the agents used LaNorme

Being an instruction rather than a hook, compliance was voluntary; it held. The
LaNorme arm ran `lanorme check` 5 to 10 times per stage, and every run wrote a
commented `lanorme.toml` in stage 1.

| Run | Starting point | Notable choices | Loosening later |
| --- | --- | --- | --- |
| 1 | hand-picked opt-in rules, every warning promoted to an error | turned on named args, docstrings, test style, similarity, a zero suppression budget; left Clean Code naming off as unidiomatic | none; later stages only updated the reasoning for staying flat |
| 2 | the `strict` profile | adopted full hexagonal layers (domain, application ports and services, infrastructure) | suppression budget 1 to 3 in stage 3, for AUTHN-001 on endpoints the contract leaves unauthenticated |
| 3 | the `strict` profile | routes in `app/api/` so the auth rule applies | per-file ignores of AUTHN-001 for the same contract-mandated endpoints |

Both loosenings fit the spec's gaming test (a rule relaxed in a stage with
findings under it), but each is a narrow, documented exemption the HTTP
contract forces, not a way around a code problem. Beyond those, no run disabled
a rule or raised a threshold after stage 1, and no snapshot in either arm
carries a `# noqa` or `# type: ignore`.

The three configs differ a good deal (one flat with hand-picked rules, two on
`strict`, one of those fully hexagonal), so the agent's self-configuration is
not stable; the resulting code was nonetheless consistently more modular.

## Reading it honestly

- Three runs per arm. The structural gap (files, longest function,
  maintainability index) has no overlap between arms at any stage, which
  is the strongest signal here; the judge's 5 to 4 split is not significant.
- Correctness was not improved and was not expected to be: LaNorme checks form,
  and the decisive bugs (subagent loophole) are semantic. The stage 3 streaming
  result is suggestive that the extra structure and test discipline helped on
  the hardest feature, but three pairs cannot separate that from chance.
- More code, not less: at stage 3 the LaNorme arm had about 25% more app SLOC
  and 2.7 times the files, for the same behaviour. Whether that is better
  factoring or ceremony is a judgement call; the judge called it better design
  in every pair, though it criticised run 2's layering as intricate.
- The judge saw LaNorme-shaped code (test-section comments, keyword-only
  arguments) after blinding, so it may have recognised a style, though it was
  never told what the style meant.

## Layout

- `runs/<arm>-<n>/stage<k>/`: the code each agent left after each stage.
- `runs/<arm>-<n>/stage<k>.transcript.jsonl`: the full agent session.
- `results/metrics.json`: every third-party measurement.
- `results/acceptance/`: hidden acceptance results per snapshot.
- `results/judge/`: the nine verdicts and the unblinding key.

## Reproducing it

```console
export AGENT_AB_WORK=/some/dir/outside/any/repo
harness/run_all.sh 3                                    # the 18 agent stages
python3.13 harness/measure.py --all runs --out results/metrics.json
python3 acceptance/run_acceptance.py runs/control-1/stage3 --stage 3
python3.13 harness/blind.py runs /tmp/blind             # key lands at /tmp/blind.key.json
harness/judge.sh /tmp/blind stage3-run1 results/judge
```
