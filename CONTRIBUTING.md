# Contributing to LaNorme

Thanks for your interest. LaNorme makes a codebase's standard executable: a
standalone, standard-library-only tool that checks quality, style, architecture,
and structure mechanically, with ready-made checks (_normes_) and an interface to
add your own. This guide covers the setup, the conventions, and how to add a rule.

## A. Where to start

The roadmap lives in the [issues](https://github.com/lanorme/lanorme/issues).
Issues tagged `help wanted` are ready for someone to pick up, and the `roadmap`
label marks the larger themes. Comment on an issue to claim it before you start,
so two people do not write the same fix. If you want to propose something new,
open an issue first and describe the rule or change, so the design can be agreed
before you write the code.

## B. Principles

- **Standard library only.** No runtime dependencies. The `dev` group holds
  `pytest` and `ruff`; the `docs` group (mkdocs-material, mike) builds the docs
  site only. A change that adds a runtime dependency will not be accepted.
- **Precision over recall.** A noisy rule trains people to ignore the tool. New
  heuristics are expected to be high precision, with the noisy or debatable
  cases left out or gated behind config.
- **LaNorme lints itself.** Ruff covers formatting (Python code blocks in
  Markdown included), trailing commas, and unused imports. `lanorme check .`
  is the gate for everything else, and the repo passes its own rules.

## C. Setup

You need [`uv`](https://docs.astral.sh/uv/) and Python 3.13 or newer.

```console
uv sync --group dev
uvx pre-commit install     # install the hooks
```

When a Python file is staged, the hooks run ruff (fix and format), `lanorme
check .` on the whole tree, and the unit tests. Five generic hooks (trailing
whitespace, end of file, YAML, TOML, merge-conflict markers) run on every
commit. A commit that touches only Markdown or TOML therefore skips the dogfood
and the tests, so run `scripts/check.sh` yourself before pushing one.
`pre-commit` is fetched on demand with `uvx`, so it is not a project
dependency.

## D. The gates

A change is ready when `scripts/check.sh` passes. It runs these gates in order:

```console
scripts/sync-agents.sh --check           # generated agent copies match their sources
uv run python scripts/gen_docs.py --check  # generated docs match the tool
uv run --group dev ruff check .          # trailing commas, unused imports
uv run --group dev ruff format --check . # formatting, Markdown code blocks included
uv run --group dev pytest tests/unit     # unit tests
uv run python evals/audit.py --version check --no-perf --output "$(mktemp)" --gate latest
uv run lanorme check .                   # dogfood: exits 0 when the tree is clean
uv build                                 # the package still builds
```

The eval audit fails when a labelled corpus is incomplete or stale, or when a
rule's holdout precision or recall drops more than 0.02 below the best any
recorded audit scored on the same holdout files, so a rule change that loses
accuracy does not pass. This fixes what ruff reports:

```console
uv run --group dev ruff check --fix . && uv run --group dev ruff format .
```

`lanorme check .` exits `1` on any failing rule. Size, complexity and
parameter-count rules are two-tier: a warning at the soft limit (exit `0`), a
failure at the hard one; the limits are listed under
[Adding or changing a check](#e-adding-or-changing-a-check). Keep even the
warnings down: refactor rather than suppress where you reasonably can.

## E. Adding or changing a check

A check is any object with `name`, `description`, `rules`, and a `check`
method that receives the `lanorme.scan.Scan` for the pass. An optional
`configure` method receives its `[tool.lanorme.<name>]` table.

```python
from lanorme import CheckResult, Violation, register
from lanorme.scan import Scan
from lanorme.sources import iter_parsed_modules


class MyCheck:
    name = "my_check"
    description = "What it enforces, in one line"
    rules = ["MYCODE-001: the rule, in one line"]

    def check(self, scan: Scan) -> CheckResult:
        violations: list[Violation] = []
        for module in iter_parsed_modules(scan.root):
            ...  # inspect module.index, module.source, module.lines
        return CheckResult.from_findings(check=self.name, violations=violations)


register(MyCheck())
```

The result's status is derived from its findings; a check never sets one.
`run(self, *, src_root)` is a deprecated entry point a plugin may define; it
runs with a `DeprecationWarning` when the plugin has no `check`. No built-in
check has one, and a new check must not add it. See
[Write a custom check](docs/how-to/write-a-check.md) for what the `Scan`
carries and how a check reads it.

Drop the module in `src/lanorme/checks/`; it is discovered and registered
automatically. Third-party checks can instead ship under the `lanorme.checks`
entry-point group or be named in `[tool.lanorme] plugins = [...]`.

Conventions for a new rule:

- **Read Python sources through `lanorme.sources`** (`iter_parsed_modules` for
  the files that parse, `iter_modules` when the check reports the ones that do
  not, with `build_unparseable_notice` building the `<PREFIX>-000` warning for
  a file that does not parse; see `docs/RULES.md` for what a `-000` code is)
  and other files through `lanorme.discovery.iter_files` / `iter_dirs`, never
  `Path.rglob` or `os.walk`, so the built-in directory pruning and the user's `exclude` globs
  are honoured. Each file is read and parsed once per run and the tree is
  shared by every check, so never read, `ast.parse` or mutate one yourself.
  `build_skip_notice` reports a file the check skips on its own.
- **Walk the tree through `module.index`,** the file's `NodeIndex`:
  `module.index.collect(ast.Call)` for the nodes of a type, `module.index.functions`
  for every def. One walk per file is shared by every check, in `ast.walk`
  order; do not call `ast.walk(tree)` yourself. Read comments, docstrings and
  imports through the shared views (`module.comments`, `module.docstrings`,
  `module.imports`) and decorator names, attribute chains and string literals
  through `lanorme.astnames`, rather than tokenising or walking again.
- **Keep run state in the `Scan`, not in globals.** The exclude globs, the
  subtree scope and the parse cache belong to the `lanorme.scan.Scan` the
  runner activates around each pass. Registered checks are templates the
  runner deep-copies and configures per pass, so a check must be
  deep-copyable and is never configured in place.
- **Build the result with `CheckResult.from_findings`,** which derives the
  status from the finding lists. Give a finding its span with
  `**locate(node)` (from `lanorme.sources`), and emit the bare code
  (`rule="SIZE-001"`): the runner expands it to the string the check declares
  in `rules`.
- **Read settings in `configure()` through `lanorme.checkconfig`**
  (`read_str_list`, `read_int`, `read_str`, `is_flag_set`) and declare the keys
  the check reads in `settings_keys: ClassVar[frozenset[str]]`. A mistyped
  value or an undeclared key is then an exit-2 config error naming the table
  and key, and `--show-config` lists the keys.
- **Raise `lanorme.errors.UsageError` for a user's mistake,** or its subclass
  `ConfigError` (carrying `key` and `source`) for one in a config file or
  table. The CLI maps both to `ERROR: ...` and exit `2`. Never print to
  stderr or call `sys.exit` outside `cli.main`; diagnostics go through
  `logging.getLogger(__name__)`.
- **One category prefix per check.** Rule codes (`SQL-001`, `LAYER-005`) are the
  public surface: people put them in `select` / `ignore` / `per-file-ignores`.
  Treat them as stable. Renaming or removing one is a breaking change.
- **Violations vs warnings.** Put a hard finding in `violations` (it fails the
  run); put an advisory in `warnings` (it reports but keeps exit 0). Advisory,
  opinionated, or stylistic rules should be warnings.
- **Cross-file checks declare `scope = "tree"`.** A check whose findings depend
  on comparing or aggregating across files (a duplicate pair, a coverage gap, an
  architecture rule) must set the class attribute `scope = "tree"`. The default
  is `"file"`. It matters under cascading per-directory config: file-scoped
  checks run once per config region, but a tree-scoped check runs once at the
  project root so a finding split across two regions is not missed.
- **Default off when opinionated or broad.** If a rule is opinionated or fires
  often on ordinary code, ship it default-off (an `enabled` field, default
  `False`) and let users opt in. Decide the default by measuring the rule on
  representative third-party code (for example the standard library), not only
  on this repo.
- **Heuristic rules get a corpus.** A fuzzy detector should come with a labelled
  corpus under `evals/corpora/` and a scorer under `evals/` that reports
  precision, recall, and F1, so the precision claim is measured. The release
  audit records those numbers to `evals/results/`; see [`evals/README.md`](evals/README.md).
- **Label first, tune second, never tune on the holdout.** A score is only
  honest if the rule was not fitted to the examples that grade it:
  - Write a case's label, with its provenance (`source`, `labelled_by`,
    `labelled_before_rule`), in `labels.json` before you tune the rule against
    that case. Never relabel a case to match what the rule does.
  - Every corpus has a `dev/` split you may tune against and a sealed `holdout/`
    split. A change to a check's thresholds or source must not add, edit,
    relabel or move that rule's holdout files in the same change. Grow the
    holdout in a separate change that leaves the rule alone.
  - A file's split is recorded per file in `labels.json`, and every label
    carries a `line_hash` of the line it labels. The hash of a file's name only
    proposes a split for a new file: `evals/validate_corpora.py --stamp` fills
    in a missing split or line hash and never overwrites a recorded one. The
    validator rejects a file on the wrong side of its recorded split, an
    unlabelled file or comment, a label with no line hash or one that no longer
    matches its line, a positive label under `negatives/` or a `positives/`
    file with none, and missing provenance.
  - Report the dev and holdout numbers side by side. A large dev-minus-holdout
    gap is overfitting to explain, not a number to tune away. The audit records
    a digest of every holdout file (its content and labels). `--gate latest`
    fails a change that removes or alters a holdout file the newest recorded
    audit holds a digest of, or that breaks the precision and recall tolerance
    under [The gates](#d-the-gates). It prints a note when it gated nothing.
  - A deliberate holdout edit (a label proved wrong) is its own reviewed
    change: an entry in the optional `evals/holdout_revisions.json` accepts one
    exact new digest per file, with a reason.
- **Stay within the house limits.** LaNorme enforces its own `SIZE` / `PARAM` /
  `COMPLEXITY` limits on itself: files warn at 300 effective lines and fail at
  500; functions warn at 50 and fail at 80; complexity warns at 10 and fails at
  15; parameters warn at 5 and fail at 8 (excluding `self` / `cls`). Split
  helpers out rather than growing one function.

## F. Tests

Tests live in `tests/unit/` and are written in clear Arrange / Act / Assert
sections (LaNorme dogfoods its own `AAA` rules on them). Shared setup goes in
`tests/unit/conftest.py` so the per-test arrange blocks stay small. Add a
positive and a negative case for each rule you touch.

## G. Documentation

Docs state the current truth of the codebase and nothing else. History lives in
`CHANGELOG.md` only: do not write "previously", "was X now Y", or "split out
from" in any doc, docstring or help text. When you add or change a
rule, update its section in `docs/RULES.md` and the rule tables in `README.md`
in the same change. The Markdown is itself linted (British spelling, no em
dashes, no emoji), so run the dogfood after editing.

`uv run python scripts/gen_docs.py` generates `lanorme.schema.json`,
`docs/reference/configuration.md`, `docs/reference/rules-index.md`, `llms.txt`,
and `llms-full.txt` from the tool itself. When you add or change a config key,
a rule, or anything else the generator reads, regenerate them in the same
change. `scripts/gen_docs.py --check` verifies them, and `scripts/check.sh`, CI and
`scripts/release.sh` all fail while they are stale.

Edit `AGENTS.md` and the skills under `.claude/skills/`, never `CLAUDE.md` or
`.agents/skills/`: those are generated copies, and `scripts/sync-agents.sh`
regenerates them.

## H. Sending a pull request

LaNorme uses the standard fork and pull-request flow, so you do not need write
access to the repository.

1. **Fork** the repository on GitHub, then clone your fork and add the main
   repository as a second remote so you can stay up to date:

   ```console
   git clone https://github.com/<your-username>/lanorme
   cd lanorme
   git remote add upstream https://github.com/lanorme/lanorme
   ```

2. **Branch** off an up-to-date `main`. Never commit to `main` itself, on your
   fork or otherwise, so it stays a clean mirror of upstream:

   ```console
   git fetch upstream
   git switch -c fix/term-cli-parity upstream/main
   ```

   Name the branch for the work: `fix/...` for a bug, `feat/...` for a new rule
   or feature, `docs/...` for documentation.

3. **Make the change**, focused on one issue. Add a positive and a negative test,
   update the rule's section in `docs/RULES.md` and the tables in `README.md` if
   you touched a rule, and add a `## [Unreleased]` entry to `CHANGELOG.md` for
   anything users would notice.

4. **Run the gates** until they pass:

   ```console
   scripts/check.sh
   ```

   The pre-commit hooks run only a subset, so run the script yourself.

5. **Commit** with a message that describes the user-facing effect, and reference
   the issue it closes:

   ```console
   git commit -m "Fix TERM parity between the CLI and the library (#17)"
   ```

6. **Push** to your fork and open a pull request against `lanorme/lanorme` on the
   `main` branch:

   ```console
   git push -u origin fix/term-cli-parity
   ```

   The push prints a link to open the pull request, or run `gh pr create`.

7. **CI** runs the same gates as `scripts/check.sh` on Python 3.13 and 3.14.
   Keep it green. A maintainer then reviews it, may ask for changes (push more
   commits to the same branch and they join the pull request), and merges it
   when it is ready.

If `main` moves on while you work, rebase your branch on `upstream/main` and
resolve any conflicts on the branch rather than in the pull request:

```console
git fetch upstream
git rebase upstream/main
```

## I. Releasing (maintainers)

Releases are automated: creating a GitHub Release publishes to PyPI through
Trusted Publishing, no token required. Use `scripts/release.sh X.Y.Z` (see the
`release-lanorme` skill in `.claude/skills/`) after adding a `## [X.Y.Z]`
section to `CHANGELOG.md`.

## J. Licence

By contributing you agree that your work is released under the MIT licence (see
the `LICENSE` file).
