# LaNorme

> *La norme*: the standard.

A configurable, pluggable architecture & code-quality linter for Python.
Point it at any project and it enforces a floor of structural and hygiene
rules; opt in to stricter, project-specific rules through configuration; and
extend it with your own checks.

```console
$ lanorme check .
```

## Install

```console
# run without installing, straight from a built wheel
uv build
uvx --from dist/lanorme-*.whl lanorme check .

# or install as a tool
uv tool install .
lanorme check .
```

Requires Python 3.13+. Zero runtime dependencies (stdlib only).

## Usage

```console
lanorme check [PATHS...]            # run all enabled checks (default path: .)
lanorme check . --check=layer_deps  # run a single check by name
lanorme check . --select TYPE,AUTHN # only these rule codes/categories
lanorme check . --ignore NAMING-003 # skip specific rules
lanorme check . --output-format json
lanorme rules                       # list every registered rule
```

Exit code is `1` when any check fails, `0` when clean.

### Inline suppression

A `# noqa` comment at the end of the source line silences every violation
that lands on that line; `# noqa: CODE1,CODE2` silences only the listed
codes (full code like `SQL-001` or a category like `SQL`):

```python
def legacy_handler(req):  # noqa: KWARG-001
    return req.text  # noqa
```

`# noqa` is recognised case-insensitively. For broader suppression (whole
directories, glob-matched paths), use `[tool.lanorme.per-file-ignores]`.

## Configuration

LaNorme discovers configuration by walking up from the target path: a dedicated
`lanorme.toml` takes precedence, otherwise a `[tool.lanorme]` table in
`pyproject.toml`. CLI flags override config.

```toml
[tool.lanorme]
select = ["ALL"]                            # rule codes/categories to run
ignore = ["NAMING-003"]                     # rule codes/categories to skip
exclude = ["build/*", "vendor/*"]           # file-path globs to exclude entirely
plugins = ["myproject.checks.house_rules"]  # extra check modules to load

# Per-file rule suppression. Keys are file-path globs; values are rule
# codes (full code or category prefix). Codes silenced here never fire
# for matching paths, but the file is still scanned (use `exclude` to
# skip the file entirely).
[tool.lanorme.per-file-ignores]
"tests/**/*.py"  = ["AAA", "SECRETPY"]
"alembic/**/*.py" = ["SQL"]
"notebooks/*.py" = ["KWARG", "DRY"]

# Per-check configuration (each table is handed to that check):
[tool.lanorme.stray_artifacts]
extensions = [".zip", ".pdf"]               # also flag these as stray (JUNK-002)
allow = ["docs/diagram.png"]                # never flag these (globs)

[tool.lanorme.forbidden_paths]
dirs = ["legacy_src", "build_artifacts"]    # these directories must not exist

[tool.lanorme.stale_paths]
tokens = ["src/", "old_pkg/"]               # flag references to these path tokens

[[tool.lanorme.domain_terms.rules]]
id = "TERM-001"
canonical = "Account"
forbidden = ["Acct", "Acnt"]
```

## Checks

Run `lanorme rules` for the full, live rule list. The full reference,
including configuration knobs and where measured the precision/recall
on the bundled corpora, is in [`docs/RULES.md`](docs/RULES.md).

**Default-on**, fire on any project without configuration:

| Category | What it catches |
|---|---|
| `CMT-001/002` | commented-out code, verbose comment blocks |
| `DRY-001` | near-duplicate function bodies (>=5 statements) |
| `SIZE-001..003` / `COMPLEXITY-001` / `PARAM-001` | file/function/class size, cyclomatic, parameter count |
| `IMPORT-001` / `ENDPOINT-001` | inline imports inside functions; over-nested endpoints |
| `NAMING-003/004` | HTTP-verb-to-handler alignment; boolean-prefix predicates |
| `TYPE-001..003` | weakly-typed `dict[str, Any]`, bare containers, untyped `**kwargs` |
| `AUTHN-001` / `SQL-001` / `SECRETPY-001` | auth dependency on mutation endpoints; raw SQL; hardcoded secrets in `.py` |
| `SHELL-001` / `DESERIAL-001` / `EVAL-001` / `CRYPTO-001` / `TLS-001` / `DEBUG-001` | shell injection; pickle/yaml.load RCE; eval/exec; weak hash; verify=False; debug-mode |
| `JUNK-001/002` | screenshots, scratch files, OS junk, stray binaries outside asset dirs |
| `PROSE-001..003` | em-dash / US spelling / emoji in Markdown (off until enabled) |
| `TESTFILE-001` | every production module under a configured directory has a `test_*.py` partner |
| `META-001..005` | self-validation that every check emits structured output |

**Opt-in**, inert unless configured or unless the rule is enabled by name:

| Rule | Why opt-in |
|---|---|
| `LAYER-001..005` | only fires on a hexagonal/layered layout (`domain/ application/ infrastructure/ api/`) |
| `PORT-001..003` | only fires on a project with `application/ports/` |
| `TERM-NNN` | needs a vocabulary configured in `[tool.lanorme.domain_terms]` |
| `PATH-001` | needs forbidden directories in `[tool.lanorme.forbidden_paths]` |
| `STALE-001` | needs stale-path tokens in `[tool.lanorme.stale_paths]` |
| `KWARG-001` | opinionated house style; opt in via `[tool.lanorme.named_args] enabled = true` |
| `NAMING-001/002` | CRUD prefixes on repositories/services; opt in via `repo_crud = true` / `service_crud = true` |
| `AAA-001/002` | comment-marker + DRY enforcement on test suites; opt in via `[tool.lanorme.test_style] enabled = true` |
| `CMT-005` | experimental restating-comment detector; precision-first but recall-limited |
| `PROSE-001/003` (on comments/docstrings) | opt in via `[tool.lanorme.comments] em_dash = true` / `emoji = true` |

## Writing a check

A check is any object implementing the `Check` protocol:

```python
from lanorme import CheckResult, Status, Violation, register


class MyCheck:
    name = "my_check"
    description = "What it enforces"
    rules = ["MYCODE-001: the rule, in one line"]

    def run(self, *, src_root: str) -> CheckResult:
        violations: list[Violation] = []
        # ... inspect files under src_root ...
        status = Status.FAIL if violations else Status.PASS
        return CheckResult(check=self.name, status=status, violations=violations)


register(MyCheck())
```

Drop the module into `lanorme/checks/` (built-in) or ship it in a package that
advertises it under the `lanorme.checks` entry-point group, or point at it from
`[tool.lanorme] plugins = [...]`. LaNorme discovers and runs it automatically.

## Versioning

LaNorme follows semantic versioning, where the public API is the set of
rule codes that may appear in `select` / `ignore` / `per-file-ignores`
and the configuration keys under `[tool.lanorme]` / `[tool.lanorme.<check>]`.
Renaming a rule code, dropping a rule, or flipping a default-on rule to
default-off is a breaking change. Adding a new rule code, or adding a new
configuration key with a sensible default, is not.

Below 1.0 (current track), breaking changes land in minor releases and are
called out in [`CHANGELOG.md`](CHANGELOG.md). After 1.0 they will be reserved
for major releases.

## License

MIT. See [`LICENSE`](LICENSE).

