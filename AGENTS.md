# AGENTS.md

Guidance for coding agents working in this repository (the
[agents.md](https://agents.md/) standard). `CLAUDE.md` and `.agents/skills/` are
generated copies kept in sync by `scripts/sync-agents.sh`; edit this file and the
skills under `.claude/skills/`, then run that script. LaNorme makes a Python codebase's standard executable, checking quality, style,
architecture, and structure mechanically on every commit. It is standalone and
standard-library-only, and it checks its own source, so changes must stay
LaNorme-compliant.

## Before you finish a change

Run the gates and make sure they pass:

```console
scripts/check.sh
```

This runs the unit tests, the dogfood lint (`lanorme check .`), and a build. It
is the same set CI and the pre-commit hooks enforce. A green run here means a
green PR. Do not finish with a red gate.

## Project facts

- Python 3.13+. Standard library only, no runtime dependencies. The only dev
  dependency is `pytest`; `pre-commit` is run through `uvx`.
- Setup: `uv sync --group dev`, then `uvx pre-commit install`.
- Layout: checks live in `src/lanorme/checks/`, the CLI in `src/lanorme/cli.py`,
  the public API and registry in `src/lanorme/__init__.py`, the shared file walk
  in `src/lanorme/discovery.py`.

## How we build features

Build a feature as a sequence of phases, not one pass, and lean on parallel
subagents wherever the work is independent (broad discovery, a multi-dimension
audit, review across several lenses):

1. Understand and design before writing: read the relevant code, and where the
   approach is open, weigh more than one design.
2. Implement the coherent change in one place; shared files do not parallelise.
3. Test end to end, not only a unit: a positive case, a negative case, the
   boundary, and a regression for the exact behaviour, plus the dogfood
   (`lanorme check .`) and a real run of the feature.
4. Review adversarially: check the change against distinct lenses (correctness,
   resilience, performance, duplication, test solidity) and verify each finding
   by reproducing it.
5. Hold to the merge-ready bar before it lands (the `merge-ready` skill).

Solo, single-pass work is for the trivial or strictly sequential: a rebase, a
one-line fix, a doc edit. Anything larger gets the phases above.

## When you touch a check

- Scan files through `lanorme.discovery.iter_py_files` / `iter_files`, never
  `Path.rglob`, so directory pruning and the user's `exclude` globs are honoured.
- One category prefix per check. Rule codes (`SQL-001`, `LAYER-005`) are the
  public surface and are stable: renaming or removing one is a breaking change.
- Put a hard finding in `violations` (fails the run) and an advisory in
  `warnings` (reports but keeps exit 0). Opinionated rules ship default-off (an
  `enabled` field defaulting to `False`).
- House limits LaNorme enforces on itself: files warn at 300 / fail at 500
  lines, functions 50 / 80, complexity 10 / 15, parameters 5 / 8. Split helpers
  out rather than growing one function.

See `CONTRIBUTING.md` for the full set (corpus discipline for heuristics, how to
choose a default by measurement, the docs rules).

## Documentation

Docs state current truth only; history lives in `CHANGELOG.md`. Update a rule's
`docs/RULES.md` section and the `README.md` tables in the same change. Markdown
is linted (British spelling, no em dashes, no emoji), so the dogfood catches
slips. See `CONTRIBUTING.md` > Documentation.

## Releasing

Add a `## [X.Y.Z]` section to `CHANGELOG.md`, then run `scripts/release.sh X.Y.Z`
(see the `release-lanorme` skill). Creating the GitHub Release auto-publishes to
PyPI through Trusted Publishing; never run `uv publish` by hand.
