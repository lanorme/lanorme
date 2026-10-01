# AGENTS.md

Guidance for coding agents working in this repository (the
[agents.md](https://agents.md/) standard). LaNorme makes a Python codebase's
standard executable, checking quality, style, architecture, and structure
mechanically on every commit. It is standalone and standard-library-only, and it
checks its own source, so changes must stay LaNorme-compliant.

`CLAUDE.md` is a generated copy of `AGENTS.md`, and `.agents/skills/` of
`.claude/skills/`, kept in sync by `scripts/sync-agents.sh`. Edit `AGENTS.md`
(never `CLAUDE.md`) and the skills under `.claude/skills/`, then run that
script.

## Before you finish a change

Run the gates and make sure they pass:

```console
scripts/check.sh
```

This runs the agent-artifact sync check, the generated-docs check, ruff (check
and format), the unit tests, the eval audit with the holdout gate, the dogfood
(`lanorme check .`), and a build. The holdout gate fails when a rule's holdout
precision or recall drops more than 0.02 below the best recorded audit (see
`CONTRIBUTING.md` > The gates). CI runs the same set on Python 3.13 and 3.14,
and the pre-commit hooks run a subset when a Python file is staged, so a green
run here means a green PR. Do not finish with a red gate.
`uv run --group dev ruff check --fix . && uv run --group dev ruff format .`
fixes what ruff reports.

For a machine-readable view of the findings use
`lanorme check . --output-format=ndjson` (one JSON object per finding,
including `severity`, `code`, `file`, `line`, `message`, `fix` and `promoted`)
and `lanorme rule CODE` for the reference section of a rule. Exit code `1`
means an error-tier finding to fix, `0` clean or advisory only, `2` a usage or
config error.

## Project facts

- Python 3.13+. Standard library only, no runtime dependencies. The dev
  dependencies are `pytest` and `ruff`; `pre-commit` is run through `uvx`.
- Setup: `uv sync --group dev`, then `uvx pre-commit install`.
- Layout: checks live in `src/lanorme/checks/`, the CLI in `src/lanorme/cli.py`,
  the run pipeline in `src/lanorme/runner.py`, the public API and registry in
  `src/lanorme/__init__.py`, the shared parse layer in `src/lanorme/sources.py`,
  the shared file walk in `src/lanorme/discovery.py`, the shared test-file
  predicates in `src/lanorme/paths.py` (use `is_test_file`; never write
  another `test_` prefix check).

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

- The entry point is `check(self, scan: Scan) -> CheckResult`; the
  `lanorme.scan.Scan` carries the root (`scan.root`), the subtree scope, the
  exclude globs, the `source_root` and the run's parse cache, and the runner
  activates it around the call. `run(*, src_root)` is the deprecated entry
  point a plugin may still define; never add one to a built-in check.
- Read Python sources through `lanorme.sources` (`iter_parsed_modules` for
  the files that parse, `iter_modules` when the check reports the ones that do
  not), which parses each file once per run and shares the tree with every
  check; never read or `ast.parse` a file yourself, and never mutate a tree.
- Walk the tree through `module.index` (`collect(ast.Call)`, `functions`), one
  shared walk per file, not `ast.walk(tree)`. Use the shared views rather than
  re-walking: `module.comments` (the single shared tokeniser pass, in
  `lanorme/comment_code.py`), `module.docstrings` /
  `module.find_docstring(node)`, `module.imports`, `module.lines`. Read
  decorator names, attribute chains and string literals through
  `lanorme.astnames`.
- Read other files through `lanorme.discovery.iter_files` / `iter_dirs`, never
  `Path.rglob` or `os.walk`, so directory pruning and the user's `exclude`
  globs are honoured.
- Discovery and sources read the active `Scan` (activated with
  `with scan.activate():`), so the helpers a check calls (`iter_files`,
  `iter_parsed_modules`) take a root, never a scan. Do not add process-global
  state.
- Registered checks are templates: the runner runs deep copies from
  `Registry.build_configured(config)`, so never configure a registered check
  in place, and keep checks deep-copyable. `register` refuses a second check
  under a taken name.
- Build the result with `CheckResult.from_findings(check=self.name, ...)`;
  the status is derived from the finding lists. Give a finding its span with
  `**locate(node)`; emit the bare code (`rule="SIZE-001"`) and let the runner
  expand it. Report a file you skip with `build_unparseable_notice` /
  `build_skip_notice` (a `<PREFIX>-000` warning) or skip it silently; never let
  an exception escape.
- Read settings in `configure()` through the `lanorme.checkconfig` readers
  (`read_str_list`, `read_int`, `read_str`, `is_flag_set`) and declare the keys
  the check reads in `settings_keys`, so a mistyped value or key is an exit-2
  config error, not a run-time failure.
- Raise `lanorme.errors.UsageError` for a mistake the user made, and its
  subclass `ConfigError` (with `key` and `source`) for one in a config file or
  table. The typed readers raise `SettingError`; only a `SettingError`,
  `TypeError` or `ValueError` raised from `configure()` is reported as the
  user's mistake (exit 2), and anything else is a bug in the check. Never print
  to stderr or call `sys.exit` outside `lanorme.cli`. Diagnostics go through
  `logging.getLogger(__name__)`; findings go to stdout through the reporters.
- Names: a function is named for what it does, verb first (`build_`,
  `collect_`, `find_`, `read_`, `is_`); modules and classes are nouns. The
  dogfood promotes NAMING-006, NAMING-007, NAMING-008 and NAMING-011 to errors
  (the `promote` list in `pyproject.toml`); NAMING-009 and NAMING-010 are
  enabled but stay warnings.
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
