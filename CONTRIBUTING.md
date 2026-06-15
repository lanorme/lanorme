# Contributing to LaNorme

Thanks for your interest. LaNorme makes a codebase's standard executable: a
standalone, standard-library-only tool that checks quality, style, architecture,
and structure mechanically, with ready-made checks (_normes_) and an interface to
add your own. This guide covers the setup, the conventions, and how to add a rule.

## Where to start

The roadmap lives in the [issues](https://github.com/lanorme/lanorme/issues).
Issues tagged `help wanted` are ready for someone to pick up, and the `roadmap`
label marks the larger themes. Comment on an issue to claim it before you start,
so two people do not write the same fix. If you want to propose something new,
open an issue first and describe the rule or change, so the design can be agreed
before you write the code.

## Principles

- **Standard library only.** No runtime dependencies. The only dev dependency
  is `pytest`. A change that adds a runtime dependency will not be accepted.
- **Precision over recall.** A noisy rule trains people to ignore the tool. New
  heuristics are expected to be high precision, with the noisy or debatable
  cases left out or gated behind config.
- **LaNorme lints itself.** There is no separate formatter or linter in the
  toolchain; `lanorme check .` is the style gate, and the repo passes its own
  rules.

## Setup

You need [`uv`](https://docs.astral.sh/uv/) and Python 3.13 or newer.

```console
uv sync --group dev
uvx pre-commit install     # install the hook; runs the checks and tests on every commit thereafter
```

The pre-commit hooks run `lanorme check .` and the unit tests, so a commit only
lands when the tree is LaNorme-compliant. `pre-commit` is fetched on demand with
`uvx`, so it is not added to the project dependencies (the only dev dependency
stays `pytest`).

## The gates

A change is ready when all three pass:

```console
uv run --group dev pytest tests/unit   # unit tests
uv run lanorme check .          # dogfood: exits 0 when the tree is clean
uv build                        # the package still builds
```

Or run all three at once:

```console
scripts/check.sh
```

`lanorme check .` exits `1` on any failing rule. Size and complexity are
two-tier: they warn at the soft limit (exit `0`) and fail at the hard limit
(500-line files, 80-line functions, complexity 15, 8 parameters). Keep even the
warnings down: refactor rather than suppress where you reasonably can.

## Adding or changing a check

A check is any object with `name`, `description`, `rules`, and a `run` method.
An optional `configure` method receives its `[tool.lanorme.<name>]` table.

```python
from lanorme import CheckResult, Status, Violation, register


class MyCheck:
    name = "my_check"
    description = "What it enforces, in one line"
    rules = ["MYCODE-001: the rule, in one line"]

    def run(self, *, src_root: str) -> CheckResult:
        violations: list[Violation] = []
        # inspect files under src_root (use lanorme.discovery.iter_py_files)
        status = Status.FAIL if violations else Status.PASS
        return CheckResult(check=self.name, status=status, violations=violations)


register(MyCheck())
```

Drop the module in `src/lanorme/checks/`; it is discovered and registered
automatically. Third-party checks can instead ship under the `lanorme.checks`
entry-point group or be named in `[tool.lanorme] plugins = [...]`.

Conventions for a new rule:

- **Scan files through `lanorme.discovery.iter_py_files` / `iter_files`,** not
  `Path.rglob`, so the built-in directory pruning and the user's `exclude`
  globs are honoured.
- **One category prefix per check.** Rule codes (`SQL-001`, `LAYER-005`) are the
  public surface: people put them in `select` / `ignore` / `per-file-ignores`.
  Treat them as stable. Renaming or removing one is a breaking change.
- **Findings vs warnings.** Put a hard finding in `violations` (it fails the
  run); put an advisory in `warnings` (it reports but keeps exit 0). Advisory,
  opinionated, or stylistic rules should be warnings.
- **Cross-file checks declare `scope = "tree"`.** A check whose findings depend
  on comparing or aggregating across files (a duplicate pair, a coverage gap, an
  architecture rule) must set the class attribute `scope = "tree"`. The default
  is `"file"`. It matters under cascading per-directory config: file-scoped
  checks run once per config region, but a tree-scoped check runs once at the
  scan root so a finding split across two regions is not missed.
- **Default off when opinionated or broad.** If a rule is opinionated or fires
  often on ordinary code, ship it default-off (an `enabled` field, default
  `False`) and let users opt in. Decide the default by measuring the rule on
  representative third-party code (for example the standard library), not only
  on this repo.
- **Heuristic rules get a corpus.** A fuzzy detector should come with a labelled
  corpus under `evals/corpora/` and a scorer under `evals/` that reports
  precision, recall, and F1, so the precision claim is measured. The release
  audit records those numbers to `evals/results/`; see [`evals/README.md`](evals/README.md).
- **Stay within the house limits.** LaNorme enforces its own `SIZE` / `PARAM` /
  `COMPLEXITY` limits on itself: files warn at 300 effective lines and fail at
  500; functions warn at 50 and fail at 80; complexity warns at 10 and fails at
  15; parameters warn at 5 and fail at 8 (excluding `self` / `cls`). Split
  helpers out rather than growing one function.

## Tests

Tests live in `tests/unit/` and are written in clear Arrange / Act / Assert
sections (LaNorme dogfoods its own `AAA` rules on them). Shared setup goes in
`tests/unit/conftest.py` so the per-test arrange blocks stay small. Add a
positive and a negative case for each rule you touch.

## Documentation

Docs state the current truth of the codebase and nothing else. History lives in
`CHANGELOG.md` only: do not write "previously", "was X now Y", or "split out
from" in `README.md`, `docs/RULES.md`, or docstrings. When you add or change a
rule, update its section in `docs/RULES.md` and the rule tables in `README.md`
in the same change. The Markdown is itself linted (British spelling, no em
dashes, no emoji), so run the dogfood after editing.

## Sending a pull request

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

   The pre-commit hook runs the same gate, so a commit only lands when the tree
   is LaNorme-compliant.

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

7. **CI** runs the unit tests and the dogfood on your pull request across the
   supported Python versions. Keep it green. A maintainer then reviews it, may
   ask for changes (push more commits to the same branch and they join the pull
   request), and merges it when it is ready.

If `main` moves on while you work, rebase your branch on `upstream/main` and
resolve any conflicts on the branch rather than in the pull request:

```console
git fetch upstream
git rebase upstream/main
```

## Releasing (maintainers)

Releases are automated: creating a GitHub Release publishes to PyPI through
Trusted Publishing, no token required. Use `scripts/release.sh X.Y.Z` (see the
`release-lanorme` skill in `.claude/skills/`) after adding a `## [X.Y.Z]`
section to `CHANGELOG.md`.

## Licence

By contributing you agree that your work is released under the MIT licence (see
the `LICENSE` file).
