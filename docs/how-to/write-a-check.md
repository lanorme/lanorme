# Write a custom check

This guide shows how to add your own rule to LaNorme as a plugin: a small Python
module that registers a check, which LaNorme then discovers and runs alongside
the built-ins. Use it to encode a house rule the bundled _normes_ do not cover,
your domain vocabulary, a project-specific structural invariant, or any
mechanical standard your team agrees on.

You do not fork LaNorme. A check is an ordinary object; you ship it in your own
package and point LaNorme at it.

## A. The Check protocol

A check is any object with four members:

- `name` (str): a unique identifier, used by `--check <name>` and in output.
- `description` (str): one line describing what the check enforces.
- `rules` (list of str): one entry per rule code, each `"CODE-001: one line"`.
- `check(self, scan: Scan) -> CheckResult`: scans the tree and returns the
  findings.

`check` receives the `lanorme.scan.Scan` for the pass: `scan.root` is the
directory being checked (a `Path`), and the scan also carries the subtree
scope, the `exclude` globs in force, the project's `source_root` and the run's
parse cache. LaNorme activates the scan around the call, so the readers in
`lanorme.sources` and `lanorme.discovery` prune what it prunes; a check reads
`scan.root` and calls them. `check` returns a `CheckResult` carrying two lists
of `Violation`: `violations` (hard findings that fail the build) and
`warnings` (advisories that report but keep the exit code at `0`).

```python
from lanorme import CheckResult, Violation
from lanorme.scan import Scan


class MyCheck:
    name = "my_check"
    description = "What it enforces, in one line"
    rules = ["MYCODE-001: the rule, in one line"]

    def check(self, scan: Scan) -> CheckResult:
        violations: list[Violation] = []
        # inspect files under scan.root
        return CheckResult.from_findings(check=self.name, violations=violations)
```

`CheckResult.from_findings(check=, violations=, warnings=)` builds the result
from the two lists, both empty by default. Its status is derived from them,
never stored: any violation is `FAIL`, otherwise any warning is `WARN`,
otherwise `PASS`, so the header LaNorme prints always agrees with the
findings. `CheckResult(check=, violations=, warnings=)` builds the same
result; its `status=` argument is deprecated and ignored, with a
`DeprecationWarning`.

`run(self, *, src_root: str)` is a deprecated entry point. A check that
defines `run` and no `check` still runs: LaNorme calls `run` with the scan
active and `src_root` set to `str(scan.root)`, and emits a
`DeprecationWarning` once per check class. Implement `check(scan)`; `run` will
be removed.

A `Violation` records where and what:

```python
Violation(
    file="src/utils.py",  # path, relative to scan.root
    line=12,  # 1-based line, or 0 for a whole-file or path finding
    rule="MYCODE-001",  # the bare code; the runner adds the description
    message="What is wrong here",
    fix="What to do about it",
)
```

`rule` may be the bare code (`"MYCODE-001"`) or the full declared string
(`"MYCODE-001: the rule, in one line"`). The runner expands a bare code to the
string the check declares in `rules`, so every output carries the description
and you spell it once per check.

Three optional fields give the finding's extent: `column` and `end_column`
(0-based, as `ast` counts them) and `end_line` (1-based, inclusive). Fill all
three from the node you report with `**locate(node)`:

```python
from lanorme.sources import locate

Violation(
    file=module.relative,
    line=node.lineno,
    rule="MYCODE-001",
    message="What is wrong here",
    fix="What to do about it",
    **locate(node),
)
```

A node without positions gives `None` for each. The JSON and ndjson records
carry these fields, derive the finding's `scope` (`span`, `line` or `file`)
from them, and the `github` format turns them into annotation columns. See
[finding records](../reference/cli.md#g2-finding-records).

## B. Register the check

Call `register()` with an instance at import time. That is what makes LaNorme
find and run it.

```python
from lanorme import register

register(MyCheck())
```

Each check needs a name of its own: registering a second, different check
under a name already taken raises `lanorme.errors.UsageError` (exit `2` at the
CLI) rather than letting the later one silently replace the earlier.
Registering the same instance twice is harmless.

The registered instance is a template. The run never configures or runs it in
place: every pass (the whole-tree pass and one per config region) works on a
deep copy configured from that pass's config, so settings cannot leak from
one region, or one run, into the next. A check must therefore survive
`copy.deepcopy`, which rules out holding an open file or a lock on the
instance.

A check that reads configuration may also implement `configure(self, *,
settings)`, which receives its `[tool.lanorme.<name>]` table before the run. See
[Configuring a check](#h-configuring-a-check) below.

## C. Read sources through `lanorme.sources`

Read Python files through `lanorme.sources`, never `Path.rglob`. The module
walks the tree with the same pruning as discovery (the built-in never-source
directories such as `.venv`, `node_modules`, `__pycache__`, `dist` and
`build`, plus the user's `exclude` globs, so an excluded subtree is never
read), decodes each file the way the interpreter does (a UTF-8 BOM and a
`coding:` cookie are honoured) and parses it once per run. Every check then
receives the same `Module`, so a run costs one parse per file rather than one
per check.

```python
from lanorme.sources import iter_parsed_modules

for module in iter_parsed_modules(scan.root):
    module.path  # Path to the *.py file
    module.relative  # its path relative to scan.root, posix style
    module.source  # the decoded text
    module.lines  # the text split into lines
    module.tree  # the parsed ast.Module
    module.comments  # every # comment, read by tokenize
    module.docstrings  # every module, class and function docstring
    module.imports  # every module an import statement names
```

`lines`, `comments`, `docstrings` and `imports` are views computed on first
use and shared with every other check that reads the same file in the run, so
reach for them rather than tokenising or walking the file again:

- `module.comments` holds one `Comment` per `#` comment (`line`, `column`,
  `token` as written, `text` without the `#`, and `standalone` when it has the
  line to itself). A `#` inside a string is never a comment. On the rare
  source the tokeniser gives up on part-way, the tuple holds the comments
  before that point and `module.has_complete_comments` is false.
- `module.docstrings` holds one `Docstring` per documented module, class and
  function, in `ast.walk` order: `owner` (the node), `node` (the string
  constant), `text` as written, `line`, and `clean()`, which equals
  `ast.get_docstring(owner)`. `module.find_docstring(node)` looks one up by its
  owner.
- `module.imports` holds one `ImportedModule` per module named: `import a, b`
  gives two, `from x import y, z` gives one for `x` holding both aliases. Each
  has `node`, `module` (`""` for `from . import y`), `aliases`, `level` and
  `is_from`.

`iter_parsed_modules(root)` yields only the files that parse. `iter_modules(root)`
yields those same `Module` objects and, for a file the parser rejects,
overflows on, or cannot read, an `UnparseableFile` (`path`, `relative`, `reason`)
so the check can decide what to do. A check that reports such files emits the
advisory `<PREFIX>-000` notice through `build_unparseable_notice`:

```python
from lanorme.sources import Module, iter_modules, build_unparseable_notice

for item in iter_modules(scan.root):
    if isinstance(item, Module):
        ...  # analyse item.tree
    else:
        warnings.append(build_unparseable_notice(prefix="MYCODE", failure=item))
```

The notice's rule is `MYCODE-000: <reason>`, with the reason one of `parse
error`, `too deeply nested` or `unreadable`. A `-000` code is a notice, not a
finding: promotion never escalates it, though the baseline records and
suppresses it like any other warning.
`build_skip_notice(prefix=, file=, name=, reason=)` builds the same notice for a
file the check skips on its own.

Trees are shared with every other check in the run, so a check must never
mutate one, or read and `ast.parse` a file itself. Copy the tree first, or
collect what you need without changing nodes.

Files that are not Python go through the discovery walk instead; see
[Check files that are not Python](#d-check-files-that-are-not-python).

### C.1 Walk the tree through `module.index`

`module.index` is the file's `NodeIndex`: every node grouped by type, from one
walk of the tree that all checks share. Ask it for the nodes you need instead
of calling `ast.walk(module.tree)`:

```python
import ast

for module in iter_parsed_modules(scan.root):
    for call in module.index.collect(ast.Call):
        ...  # every call in the file
    for function in module.index.functions:
        ...  # every def and async def, nested ones included
```

`collect(*types)` returns the nodes whose exact type is one of `types`.
`functions` is `collect(ast.FunctionDef, ast.AsyncFunctionDef)`. Both keep
`ast.walk` order, so findings come out in the order a walk would give.

### C.2 Read names off nodes through `lanorme.astnames`

`lanorme.astnames` answers the small questions many checks ask of a node:

- `find_decorator_leaf(decorator)` gives the name a decorator resolves to
  (`@app.route("/")` gives `route`, `@abc.abstractmethod` gives
  `abstractmethod`), or `None`. Calls are looked through unless `calls=False`;
  subscripts too with `subscripts=True`. `list_decorator_leaves(node)` gives
  one per decorator of a function or class.
- `build_attr_chain(node)` gives the dotted path an attribute spells
  (`hashlib.md5` gives `("hashlib", "md5")`), or `()`.
- `read_str_constant(node)` gives the value of a string literal, or `None`.

### C.3 The run context: `lanorme.scan.Scan`

`iter_files`, `iter_dirs` and `iter_modules` honour the run's exclude globs
and subtree scope, and all of them, `parse_module` included, share one parse
cache, but a check never passes these around: they belong to the current `Scan`, the one `check`
receives, which LaNorme activates around the call. A check reads `scan.root`
and calls the functions. To run a check by hand under the same confinement,
hand `lanorme.run_check` the scan; it activates the scan around the call and
isolates an exception as a `RUN-000` notice, as a full run does:

```python
from lanorme import run_check
from lanorme.scan import Scan

result = run_check(MyCheck(), scan=Scan(root=project_root, excludes=("vendor/*",)))
```

`run_check(check, src_root=str(path))` runs it over that path under whatever
scan is active, and `lanorme.run_all` takes the same two arguments for every
registered check. A direct `MyCheck().check(scan)` runs under the scan in
force, not the one passed, so activate it first: `with scan.activate():`.
`Scan.restrict(scope=..., excludes=...)` gives a scan confined to a subtree
with more globs, sharing the parse cache. With no scan active, the whole tree
is walked with no excludes. `lanorme.scan.get_current_scan()` returns the
scan in force.

The current scan lives in a `contextvars.ContextVar`, so it follows the
context. Whether a new thread starts in its creator's context depends on the
Python version and build (Python 3.14 adds the `thread_inherit_context` flag),
and a worker outside it walks the whole tree with no excludes and another
parse cache. A check that hands work to threads should run each task in a copy
of its own context:

```python
import contextvars
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor() as pool:
    futures = [pool.submit(contextvars.copy_context().run, scan_part, part) for part in parts]
```

Take one `copy_context()` per task: a context can be entered by only one
thread at a time.

## D. Check files that are not Python

Only Python goes through the parse layer. Every other file type goes through
the discovery walk, which applies the same pruning and the user's `exclude`
globs: `lanorme.discovery.iter_files(root, suffix=".sh")` returns the matching
paths, sorted, and `iter_dirs(root)` the directories. Omit `suffix` to see
every file. The check reads each file itself, decides per line, and builds
each `Violation` with the file's root-relative posix path. `line=0` with no
column marks a whole-file finding (its `scope` is `file`); a line number, with
`column`, `end_line` and `end_column` when known, marks a line or a span. A
file the check cannot read gets the same `<PREFIX>-000` notice as an
unparseable Python file, through `build_skip_notice` with `reason=UNREADABLE`.

This check reads every `*.sh` in the tree. It fails a script that never
enables strict mode and warns once per line that uses a backtick command
substitution:

```python
# shell_rules.py
from __future__ import annotations

from lanorme import CheckResult, Violation, register
from lanorme.discovery import iter_files
from lanorme.scan import Scan
from lanorme.sources import UNREADABLE, build_skip_notice

STRICT_MODE = "set -euo pipefail"


class ShellRules:
    name = "shell_rules"
    description = "House rules for shell scripts"
    rules = [
        "SH-001: A shell script enables strict mode (set -euo pipefail)",
        "SH-002: Command substitution uses $(...) rather than backticks",
    ]

    def check(self, scan: Scan) -> CheckResult:
        violations: list[Violation] = []
        warnings: list[Violation] = []
        for path in iter_files(scan.root, suffix=".sh"):
            relative = path.relative_to(scan.root).as_posix()
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                warnings.append(
                    build_skip_notice(
                        prefix="SH", file=relative, name=path.name, reason=UNREADABLE
                    ),
                )
                continue
            if not any(line.strip() == STRICT_MODE for line in lines):
                violations.append(
                    Violation(
                        file=relative,
                        line=0,
                        rule="SH-001",
                        message="Script does not enable strict mode",
                        fix=f"Add '{STRICT_MODE}' after the shebang",
                    ),
                )
            for lineno, line in enumerate(lines, start=1):
                column = line.find("`")
                if column >= 0 and not line.lstrip().startswith("#"):
                    warnings.append(
                        Violation(
                            file=relative,
                            line=lineno,
                            column=column,
                            rule="SH-002",
                            message="Backtick command substitution",
                            fix="Use $(...) so substitutions nest and read clearly",
                        ),
                    )
        return CheckResult.from_findings(
            check=self.name,
            violations=violations,
            warnings=warnings,
        )


register(ShellRules())
```

With a `scripts/deploy.sh` that substitutes a command with backticks on its
third line and never enables strict mode:

```console
$ lanorme check . --plugin shell_rules --check shell_rules
[FAIL] shell_rules
  VIOLATION: scripts/deploy.sh:0 — Script does not enable strict mode
    Rule: SH-001: A shell script enables strict mode (set -euo pipefail)
    Fix: Add 'set -euo pipefail' after the shebang
  WARNING: scripts/deploy.sh:3 — Backtick command substitution
    Rule: SH-002: Command substitution uses $(...) rather than backticks
    Fix: Use $(...) so substitutions nest and read clearly
--- shell_rules: 1 violations, 1 warnings ---

Summary: 1 checks — 0 passed, 0 warned, 1 failed.
Findings: 1 error to fix, 1 advisory warning.
```

In the `ndjson` output the violation carries `"scope": "file"` and the
warning `"line": 3, "column": 8, "scope": "line"`.

### D.1 Markdown

`lanorme.markdown` holds the prose surface the built-in `prose` and `docs`
checks share, so a rule written on it agrees with them about what is prose.
`iter_prose_lines(lines)` yields `(lineno, line)` for every line outside YAML
front matter and fenced code blocks, fence lines excluded, and
`strip_inline_code(line)` blanks inline code spans while keeping every column
in place:

```python
from lanorme.discovery import iter_files
from lanorme.markdown import iter_prose_lines, strip_inline_code

for path in iter_files(scan.root, suffix=".md"):
    lines = path.read_text(encoding="utf-8").splitlines()
    for lineno, line in iter_prose_lines(lines):
        text = strip_inline_code(line)
        ...  # match the rule against prose only
```

The built-in checks that read files other than Python are the models to copy:
`prose` (Markdown by default, any extension its `extensions` list names),
`docs` (the layout of the docs tree), `skills` (`SKILL.md` against the Agent
Skills specification), and `stray_artifacts` and `forbidden_paths` (any file,
by name or location).

## E. Conventions

These conventions keep a custom check consistent with the built-ins. The same
rules apply whether the check ships inside LaNorme or as your plugin.

- **One category prefix per check.** A check owns a single rule-code family
  (`MYCODE-001`, `MYCODE-002`, ...). Codes are the public surface: people put
  them in `select`, `ignore`, and `per-file-ignores`, so treat them as stable.
- **Hard findings in `violations`, advisories in `warnings`.** A `violations`
  entry fails the run (exit code `1`); a `warnings` entry reports but leaves the
  exit code at `0`. Opinionated or stylistic rules belong in `warnings`, so a
  user can promote them to errors when they choose (see
  [`promote`](../reference/configuration.md#promote)).
- **Fill the finding lists; never set a status.** The status is derived from
  `violations` and `warnings`, so the header LaNorme prints (`[FAIL]`,
  `[WARN]`, `[PASS]`) always agrees with the findings.
- **Cross-file checks declare `scope = "tree"`.** If a finding depends on
  comparing or aggregating across files, set the class attribute `scope =
  "tree"`. The default `"file"` scope lets a check run once per config region
  under per-directory configuration; a tree-scoped check runs once at the
  project root so a finding split across two regions is not missed. A region
  pass still starts at the project root and sees only that region's files, so
  `module.relative` keeps the full path (`tests/helpers.py`, not
  `helpers.py`) and a path-based exemption such as `tests/` holds inside a
  nested region.
- **Default off when opinionated or broad.** A rule that fires often on ordinary
  code should ship default-off behind an `enabled` flag, so users opt in (see
  [Configuring a check](#h-configuring-a-check)).
- **Raise `UsageError` for a user's mistake.** A setting that makes no sense is
  not a crash. Raise `lanorme.errors.ConfigError` (a `UsageError` that also
  carries the offending `key` and the `source` table) from `configure`, or
  `UsageError` for any other mistake of the user's, with the message the user
  needs; the CLI prints it as `ERROR: ...` and exits `2`. A `UsageError`
  raised from `check` is not hidden in a `RUN-000` notice either. Never print
  to stderr or call `sys.exit` from a check.

A check must never let an exception escape `check`. LaNorme isolates a check
that raises and reports it as a `RUN-000` warning whose message carries the
exception type and text, so one bug cannot sink the whole run. A clean check
should not rely on that safety net.

## F. A worked example

This check fails when a module is named exactly `utils.py`, on the house rule
that every module should be named after what it does. LaNorme ships the same
rule built in as `NAMING-010` in the opt-in `naming_clean_code` check; it stays
here as the example because it is the smallest complete check.

```python
# house_rules.py
from __future__ import annotations

from lanorme import CheckResult, Violation, register
from lanorme.scan import Scan
from lanorme.sources import iter_parsed_modules


class NoUtilsModule:
    name = "no_utils_module"
    description = "Modules must have a meaningful name, not 'utils'"
    rules = ["HOUSE-001: Module must not be named 'utils.py'"]

    def check(self, scan: Scan) -> CheckResult:
        violations: list[Violation] = []
        for module in iter_parsed_modules(scan.root):
            if module.path.name == "utils.py":
                violations.append(
                    Violation(
                        file=module.relative,
                        line=0,
                        rule="HOUSE-001",
                        message="Module named 'utils.py' has no clear responsibility",
                        fix="Rename it after what it actually does",
                    )
                )
        return CheckResult.from_findings(check=self.name, violations=violations)


register(NoUtilsModule())
```

With `house_rules.py` importable (on `sys.path` or installed), a
`pyproject.toml` at the project root and a `src/utils.py`, load it with
`--plugin` and run it. Paths are relative to the project root, the directory
holding the config file:

```console
$ lanorme check src/ --plugin house_rules --check no_utils_module
[FAIL] no_utils_module
  VIOLATION: src/utils.py:0 — Module named 'utils.py' has no clear responsibility
    Rule: HOUSE-001: Module must not be named 'utils.py'
    Fix: Rename it after what it actually does
--- no_utils_module: 1 violations, 0 warnings ---

Summary: 1 checks — 0 passed, 0 warned, 1 failed.
Findings: 1 error to fix, 0 advisory warnings.
```

The check emitted the bare code `HOUSE-001`; the report shows the full rule
string from `rules`.

The exit code is `1`. Rename or remove the file and the run is clean:

```console
$ lanorme check src/ --plugin house_rules --check no_utils_module
All 1 checks passed.
```

The exit code is `0`.

!!! note
    `--plugin` is repeatable (`--plugin a --plugin b`), not comma-separated.
    Pass the dotted module path, for example `--plugin myproject.checks.house_rules`.

### F.1 Make it an advisory

To report without failing the build, collect the findings in a `warnings`
list instead and pass it as `warnings`:

```python
return CheckResult.from_findings(check=self.name, warnings=warnings)
```

The run then exits `0`, the check shows as `[WARN]` and each finding is
labelled `WARNING:` rather than `VIOLATION:`. A user who wants it to
fail the build can escalate the code with
[`promote`](../reference/configuration.md#promote):

```console
$ lanorme check src/ --plugin house_rules --promote HOUSE-001
```

That turns the advisory into a build-failing error (exit code `1`). `promote =
["ALL"]` escalates every advisory at once. A code in `promote` must be one a
loaded check declares, so load the plugin in the same run; otherwise the run
exits `2` with `'promote' names no known rule code or category`.

## G. Loading the plugin

LaNorme has three ways to load a plugin module so its `register()` call runs.
Choose one.

### G.1 Name it in config

List the module under `plugins` in `[tool.lanorme]`. LaNorme imports each named
module before the run, so the check self-registers:

```toml
[tool.lanorme]
plugins = ["myproject.checks.house_rules"]
```

This is the usual choice for a check that lives in your own repository. The
[`plugins` reference](../reference/configuration.md#plugins) documents the key.

### G.2 Ship it under the entry-point group

A distributable package can advertise its check module under the
`lanorme.checks` entry-point group. Any environment that installs the package
then has the check available with no per-project config:

```toml
# in the plugin package's pyproject.toml
[project.entry-points."lanorme.checks"]
house-rules = "myproject.checks.house_rules"
```

The entry-point value is the dotted module path; LaNorme imports it on every run.

### G.3 Pass it on the command line

Use `--plugin` for a one-off run, a quick experiment, or CI wiring that prefers
explicit flags over config:

```console
$ lanorme check src/ --plugin myproject.checks.house_rules
```

`--plugin` adds to whatever `plugins` already lists; it does not replace the
list.

## H. Configuring a check

To accept settings from a `[tool.lanorme.<name>]` table, implement an optional
`configure` method. LaNorme hands it the table (a dict) before the run.

Read each value through the typed readers in `lanorme.checkconfig`:
`is_flag_set` for a boolean, `read_int`, `read_str` and `read_str_list`. Each
returns the value or rejects the wrong type (a quoted number, a float or `true`
for an integer, a bare string for a list). Declare the keys `configure` reads
in `settings_keys`. LaNorme then refuses a key outside that set, and
`--show-config` lists the set on the check's `keys:` line. Both mistakes exit
`2` and name the table and the key.

A reader rejects a value by raising `lanorme.checkconfig.SettingError` (a
`TypeError`), and a `configure` that validates a value itself raises
`TypeError` or `ValueError`; LaNorme reports any of these as a
`ConfigError` naming the table and, where it can isolate it, the key. Any
other exception out of `configure` (an `AttributeError`, a `KeyError`) is a
bug in the check and surfaces as one, not as the user's mistake.

```python
# stray_extensions.py
from dataclasses import dataclass, field
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import is_flag_set, read_str_list
from lanorme.discovery import iter_files
from lanorme.scan import Scan


@dataclass
class StrayExtensions:
    name: str = "stray_extensions"
    description: str = "Flag unwanted file extensions in the tree"
    enabled: bool = False
    extensions: tuple[str, ...] = ()
    rules: list[str] = field(default_factory=lambda: ["HOUSE-002: Unwanted file extension"])
    settings_keys: ClassVar[frozenset[str]] = frozenset({"enabled", "extensions"})

    def configure(self, *, settings: dict[str, object]) -> None:
        self.enabled = is_flag_set(settings=settings, key="enabled", default=self.enabled)
        self.extensions = read_str_list(
            settings=settings, key="extensions", default=self.extensions
        )

    def check(self, scan: Scan) -> CheckResult:
        if not self.enabled:
            return CheckResult.from_findings(check=self.name)
        root = scan.root
        warnings = [
            Violation(
                file=path.relative_to(root).as_posix(),
                line=0,
                rule="HOUSE-002",
                message=f"File with unwanted extension {suffix}",
                fix="Delete it, or keep it out of the repository",
            )
            for suffix in self.extensions
            for path in iter_files(root, suffix=suffix)
        ]
        return CheckResult.from_findings(check=self.name, warnings=warnings)


register(StrayExtensions())
```

A user then configures it under the check's own table, named after `self.name`:

```toml
[tool.lanorme.stray_extensions]
enabled = true
extensions = [".zip", ".tmp"]
```

With `stray_extensions.py` importable and `src/old.zip` and `src/notes.tmp` in
the tree:

```console
$ lanorme check . --plugin stray_extensions --check stray_extensions
[WARN] stray_extensions
  WARNING: src/old.zip:0 — File with unwanted extension .zip
    Rule: HOUSE-002: Unwanted file extension
    Fix: Delete it, or keep it out of the repository
  WARNING: src/notes.tmp:0 — File with unwanted extension .tmp
    Rule: HOUSE-002: Unwanted file extension
    Fix: Delete it, or keep it out of the repository
--- stray_extensions: 0 violations, 2 warnings ---

Summary: 1 checks — 0 passed, 1 warned, 0 failed.
Findings: 0 errors to fix, 2 advisory warnings.
```

A mistyped value or key stops the run before any check starts:

```console
$ cat pyproject.toml
[tool.lanorme.stray_extensions]
enabled = true
extension = [".zip"]
$ lanorme check . --plugin stray_extensions
ERROR: unknown key in [tool.lanorme.stray_extensions]: 'extension'.
  Keys this check reads: enabled, extensions.
$ echo $?
2
```

An opt-in check defaults `enabled` to `false` and returns an empty result
(`CheckResult.from_findings(check=self.name)`) until the table sets
`enabled = true`. That keeps a broad or opinionated rule inert on a project
that has not asked for it. The concise summary counts such checks on its
`Opt-in checks not enabled:` line, and `--check` on one prints a note saying
it is off.

When only some of a check's rules wait on a setting, declare them in a class
attribute `opt_in_rules: frozenset[str]` and, to name the setting, an
`opt_in_settings: dict[str, str]` from code to key. `lanorme rule CODE` then
reports the rule as `opt-in via <key> = true in [tool.lanorme.<check>]`
instead of `on by default`, as `comments` does for `PROSE-001` (`em_dash`).

For a mistake `configure` cannot express as a type, such as two settings that
contradict each other, raise `lanorme.errors.ConfigError` with the `key` and
`source` it concerns. The CLI reports it the same way, with exit `2`.

## I. Verify it is loaded

Run the check and confirm it executed. `--output-format full` shows passing
checks too, so a loaded check appears even when it found nothing:

```console
$ lanorme check . --plugin myproject.checks.house_rules --output-format full
```

For a check loaded via config or the entry-point group, drop the `--plugin`
flag: plain `lanorme check . --output-format full` lists it once it is
registered. Seeing your check in that output (a `[PASS]` line when it is clean)
is the reliable signal that the plugin loaded. For machine-readable output while
developing, use `--output-format ndjson` (one finding per line) or
`--output-format json` (one object per check).

`lanorme rules` is a narrower check. It lists only rules registered through the
`lanorme.checks` entry-point group, so it surfaces a plugin shipped that way but
not one loaded via `[tool.lanorme] plugins` or `--plugin`. The `rules` command
also takes no `--plugin` flag.

## J. Related pages

- [Configuration reference](../reference/configuration.md): every
  `[tool.lanorme]` key, including `plugins`, `select`, `ignore`, and `promote`.
- [Rule reference](../RULES.md): what each built-in rule catches and how to
  configure it; a model for documenting your own.
