# Configure which checks run

Recipes for narrowing what LaNorme runs and silencing the noise you have
decided to accept. Each recipe gives the goal, the `[tool.lanorme]` config,
the equivalent command-line flag where one exists, and a verified example.

Config is read from `[tool.lanorme]` in `pyproject.toml`, or from a
`lanorme.toml` / `.lanorme.toml` file. Command-line flags override config for
a single run. For the full key list and types, see the
[configuration reference](../reference/configuration.md). Rule codes and
categories are listed by `lanorme rules`; per-check settings live in the
[rule reference](../RULES.md).

Targets are rule codes (`EVAL-001`), categories (`SEC`, `SECRETPY`), or `ALL`.

## Run only some checks

Goal: run a chosen subset and skip everything else.

```toml
[tool.lanorme]
select = ["SECRETPY", "EVAL-001"]
```

Equivalent flag: `--select` takes a comma-separated list.

```console
$ lanorme check src --select EVAL-001
[FAIL] security_calls
  VIOLATION: src/pkg/main.py:1 — eval() on a non-literal argument is an RCE primitive
    Rule: EVAL-001
    Fix: Use ast.literal_eval for trusted-shape parsing, or build a dispatch table
--- security_calls: 1 violations, 0 warnings ---

Summary: 24 checks — 23 passed, 0 warnings, 1 failed.
```

A category selects every code under it: `--select SEC` runs the whole
security group; `--select ALL` runs everything enabled.

>!!! note
>    Opt-in checks stay off even when named in `select`. Enable them first
>    with `[tool.lanorme.<check>] enabled = true` (for example
>    `[tool.lanorme.prose]`). See the [rule reference](../RULES.md).

## Skip a rule everywhere

Goal: keep the full run but drop one rule (or one category) you do not want.

```toml
[tool.lanorme]
ignore = ["PARAM-001"]
```

Equivalent flag: `--ignore` takes a comma-separated list.

```console
$ lanorme check src --select file_limits --ignore PARAM-001
All 24 checks passed.
```

`ignore` applies after `select`, so a rule that is both selected and ignored
is skipped.

## Exclude paths from the scan

Goal: never walk certain files, such as generated code or migrations.

```toml
[tool.lanorme]
exclude = ["**/migrations/*", "generated/*"]
```

Equivalent flag: `--exclude` takes a comma-separated list of globs.

Globs match the path relative to the project root. A leading `**/` matches a
directory at any depth, so `**/migrations/*` excludes `src/pkg/migrations/`:

```console
$ lanorme check . --select EVAL-001
  VIOLATION: src/pkg/main.py:1 — eval() on a non-literal argument is an RCE primitive
  VIOLATION: src/pkg/migrations/m.py:1 — eval() on a non-literal argument is an RCE primitive

$ lanorme check . --select EVAL-001 --exclude '**/migrations/*'
  VIOLATION: src/pkg/main.py:1 — eval() on a non-literal argument is an RCE primitive
```

Match the depth you have. To exclude a `migrations/` directory sitting at the
project root, use `migrations/*`; the leading `**/` form needs a parent
segment and will not match a top-level directory.

## Silence a rule for a path glob

Goal: keep a rule on, but accept it for files matching a glob (for example,
allow hardcoded fixture tokens under `tests/`).

This is config only; there is no command-line equivalent.

```toml
[tool.lanorme]
select = ["SECRETPY"]

[tool.lanorme.per-file-ignores]
"tests/*" = ["SECRETPY-001"]
```

The key is a glob; the value is a list of codes or categories suppressed for
matching files. With the config above, only the production file reports:

```console
$ lanorme check .
[FAIL] secrets
  VIOLATION: src/app.py:1 — Hardcoded credential value bound to 'PASSWORD'
    Rule: SECRETPY-001: No hardcoded secrets in source code
    Fix: Read the value from an environment variable, secrets manager, or settings module
--- secrets: 1 violations, 0 warnings ---

Summary: 24 checks — 23 passed, 0 warnings, 1 failed.
```

The matching `tests/test_app.py` carries the same hardcoded token but is not
reported. Confirm the discovered config and effective per-check settings with
`lanorme check --show-config`.

## Silence one line

Goal: accept a single finding in place, at the source line.

This is a source pragma; there is no command-line equivalent.

- `# noqa` suppresses every finding on that line.
- `# noqa: CODE` suppresses only that rule code on that line; other findings
  on the line still report, and a different code does not suppress.

Given this file checked with `--select EVAL-001`:

```python
def f():
    x = eval(input())                 # line 2: reported

def g():
    y = eval(input())  # noqa         # line 7: silenced (all rules)

def h():
    z = eval(input())  # noqa: EVAL-001   # line 12: silenced (this code)

def k():
    w = eval(input())  # noqa: SQL-001    # line 17: still reported
```

Only lines 2 and 17 are reported: the bare `# noqa` and the matching
`# noqa: EVAL-001` suppress their lines, while `# noqa: SQL-001` is the wrong
code and does not silence the `eval`.

```console
$ lanorme check a.py --select EVAL-001
[FAIL] security_calls
  VIOLATION: a.py:2 — eval() on a non-literal argument is an RCE primitive
  VIOLATION: a.py:17 — eval() on a non-literal argument is an RCE primitive
--- security_calls: 2 violations, 0 warnings ---

Summary: 24 checks — 23 passed, 0 warnings, 1 failed.
```

## Exit codes

`lanorme check` exits `0` when clean, `1` when there are findings, and `2` on
a usage or config error. This drives CI: a silenced finding leaves the run
clean.

```console
$ lanorme check src --select EVAL-001   # no eval in src
All 24 checks passed.
$ echo $?
0
```

## See also

- [Configuration reference](../reference/configuration.md) for every
  `[tool.lanorme]` key, including `promote`, `extends`, `baseline`,
  `source_root`, and `plugins`.
- [Rule reference](../RULES.md) for what each code catches and its per-check
  settings.
