# Agent A/B: does LaNorme change the code agents write?

## Question

Does an Opus coding agent told to use LaNorme write better code than the same
agent without it? "Better" is judged by third-party tools, a blind reviewer, and
a human reading the code. LaNorme itself is never used to score, because that
would only measure whether the agent obeyed LaNorme.

## Arms

Both arms run the same model (Opus 5.5) on the same prompts, each run in a fresh
directory with no access to this repository and no mention of LaNorme anywhere
in its context except where stated.

Agents run headless with permission prompts off, so a run must happen in a
disposable environment (a throwaway container or virtual machine with no
credentials worth protecting). `harness/run_stage.sh` refuses to start until
`AGENT_AB_DISPOSABLE_ENVIRONMENT=1` confirms it.

| Arm | Project `CLAUDE.md` | LaNorme config |
| --- | --- | --- |
| `control` | the shared baseline (`arms/baseline.md`) | none |
| `lanorme` | the baseline plus one paragraph (`arms/lanorme.md`) | none supplied; the agent writes its own |

The LaNorme paragraph tells the agent to explore the tool (`lanorme rules`,
`lanorme rule CODE`, the docs index at `lanorme.github.io/lanorme/llms.txt`),
write a `lanorme.toml` suited to the project (including which default-off
opinionated rules to enable), and run `uvx lanorme@0.21.0 check .` and fix what
it reports before finishing. The agent may revise its config in later stages.
It is an instruction only: no hook enforces it.

## Tasks

One product, grown over three stages. Each stage is a fresh agent session in
that run's own codebase, so stages 2 and 3 work on code the arm wrote itself.

1. **Build**: a FastAPI service serving a LangChain `deepagents` conversational
   agent behind guardrails (input and output PII redaction, a topic blocklist,
   a per-turn tool-call limit).
2. **Tenants**: per-tenant guardrail policies with admin endpoints.
3. **Streaming and memory**: server-sent-event streaming with guardrails still
   applied, and per-session conversation memory.

The prompts (`prompts/stage{1,2,3}.md`) are medium detail. They fix the public
HTTP contract and the `create_app(model=...)` seam so one acceptance suite can
drive every run, and leave the internal design to the agent. No LLM API key is
available, so both arms are told to test against LangChain fake chat models.

## Scale

3 runs per arm, 2 arms, 3 stages: 6 codebases, 18 stage snapshots.

## Measurements

Every snapshot gets the same third-party measurements:

| Area | Tool | Reported |
| --- | --- | --- |
| Complexity | radon cc, radon mi | mean and max cyclomatic complexity, functions graded C or worse, maintainability index |
| Size | radon raw | source lines, files, function lengths |
| Correctness | pytest + coverage | the agent's own tests: pass rate, line coverage |
| Correctness | hidden acceptance suite (`acceptance/`) | pass rate per stage, same tests for every run |
| Lint | ruff (broad ruleset) | findings by category |
| Types | mypy | error count |
| Security | bandit | findings by severity |
| Duplication | pylint duplicate-code | duplicated blocks and lines |
| Dead code | vulture | unused-code findings |
| Process | transcript | wall time, turns, cost per stage |

For the `lanorme` arm only, also recorded:

- the `lanorme.toml` at each stage and its diff from the previous stage;
- a gaming flag: a rule disabled, a threshold raised, or a path excluded in a
  stage where the agent had findings under that rule;
- compliance: how many times the agent actually ran `lanorme`, per stage;
- how much the three runs' configs differ from each other.

## Blind judge

For each stage and each run index, a fresh Opus agent receives the two
codebases as `A` and `B` in random order, with `lanorme.toml` removed and
LaNorme mentions stripped. It scores each 1 to 10 on correctness, design,
readability and maintainability, citing `file:line` evidence, and picks a
winner. That is 9 verdicts, unblinded only after all are collected.

## Layout

```
experiments/agent-ab/
  SPEC.md                    this file
  arms/                      the CLAUDE.md for each arm
  prompts/                   the stage prompts, verbatim
  acceptance/                the hidden acceptance suite
  harness/                   run, measure and judge scripts
  runs/<arm>-<n>/stage<k>/   snapshot of each codebase after each stage
  runs/<arm>-<n>/stage<k>.transcript.jsonl
  results/metrics.json       every measurement
  results/judge/             the verdicts
  README.md                  the results and what they show
```

`runs/` is not committed here: the published runs are in
lanorme/lanorme-experiments under `agent-ab/runs/`, and git ignores a local
copy. It is also excluded from the dogfood lint, ruff and pytest collection.

## Limits

Three runs per arm shows whether a difference is consistent, not that it is
statistically significant. The fixed HTTP contract narrows the design space a
little to make the acceptance suite possible.
