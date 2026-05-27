# LaNorme rule reference

One section per rule code emitted by LaNorme. Each section says what the
rule catches, what it does not, where to configure it, and (where the
rule has a labelled corpus under `tests/fixtures/` and a scorer under
`benchmarks/`) the measured precision / recall / F1 on that corpus.

Live rule list: `lanorme check . --check=rules` (or `lanorme rules`).
Default policy and per-check configuration: see the README.

The rules are grouped by category in the same order as `lanorme rules`.

---

## Comments: `CMT-*` and `PROSE-*` on .py

### `CMT-001`: No commented-out code

Default-on. Walks every `#` comment and parses its text as Python; if the
result is a `Import` / `ImportFrom` / `Assign` / `AnnAssign` / `AugAssign`
/ `FunctionDef` / `AsyncFunctionDef` / `ClassDef` / `For` / `While` /
`With` / `Delete` / `Raise` / `Assert` / `Expr(Call(...))`, the comment is
treated as disabled code. Guards: comments ending in `.` / `?` / `!` / `:`
are prose; `foo(...)` (literal `...`) is illustrative; `label: type` with
no value is documentation.

Measured against the 165-comment corpus under
`tests/fixtures/comments_commented_code/` with `benchmarks/score_cmt001.py`:
**P = 0.978 / R = 0.667 / F1 = 0.793** (TP = 44, FP = 1, FN = 22). The
single FP is an illustrative call signature. The 22 FNs are dominated by
standalone block headers (`if x:`, `def f():`, decorator lines, bare
`return`) which `ast.parse` rejects without their body.

Config: none.

### `CMT-002`: No verbose comments

Default-on. Flags any single comment longer than `max_comment_chars`
(default 120) and any block of consecutive standalone comments longer
than `max_block_lines` (default 6).

Config:
```toml
[tool.lanorme.comments]
max_comment_chars = 120
max_block_lines   = 6
```

### `CMT-005`: No comments that restate the next line of code

Default-off. **Experimental.** Precision-funnel detector: AST adjacency
+ 11-category allowlist + stem-equality + verb-to-AST-node table +
asymmetric coverage floor. Designed precision-first; expects to miss
synonym paraphrases.

Measured against the 167-comment corpus under
`tests/fixtures/comments_restating/` with `benchmarks/score_cmt005.py`,
post audit-broadening: **P = 1.000 / R = 0.418 / F1 = 0.589** (TP = 33,
FP = 0, FN = 46, TN = 88). The recall drop versus an earlier 0.647 came
from extending the corpus into categories the design said it would miss
(stale comments, half-useful-qualifier, type-in-prose, multi-line
restatement, docstring duplication); precision held at 1.000.

Config:
```toml
[tool.lanorme.comments]
restating = true
```

### `PROSE-001` / `PROSE-003` on comments and docstrings

Off until enabled. The same rule codes that the `prose` check emits on
Markdown also fire here, on `#` comments and `"""..."""` docstrings,
when configured.

Config:
```toml
[tool.lanorme.comments]
em_dash = true   # emit PROSE-001 on comments/docstrings
emoji   = true   # emit PROSE-003 on comments/docstrings
```

---

## Domain terminology: `TERM-NNN`

Configurable ubiquitous-language enforcement. Inert by default. Each
rule the user configures gets a code from the `TERM-` family.

Config:
```toml
[[tool.lanorme.domain_terms.rules]]
id        = "TERM-001"
canonical = "Account"
forbidden = ["Acct", "Acnt"]

[[tool.lanorme.domain_terms.rules]]
id        = "TERM-002"
canonical = "Customer"
forbidden = ["Cust", "Client"]
```

---

## Duplication: `DRY-001`

Default-on. Functions with identical normalised AST bodies and at least
five statements are flagged as duplicates. AST normalisation strips
variable names so that two functions that differ only in identifier
spelling are still detected.

Config: none currently. False positives on intentionally parallel
adapters across bounded contexts are a known limit; suppress them with
`[tool.lanorme.per-file-ignores]` or `# noqa: DRY-001`.

---

## File limits: `SIZE-*` / `COMPLEXITY-001` / `PARAM-001`

All default-on.

- `SIZE-001`: Python files. Warn at 300 effective (non-blank,
  non-comment) lines; error at 500.
- `SIZE-002`: functions and methods. Warn at 50 lines; error at 80.
- `SIZE-003`: classes with more than 10 methods (warning only). Useful
  as a smell on services and views; on rich aggregate roots in a DDD
  codebase, expect to silence it via `per-file-ignores`.
- `COMPLEXITY-001`: cyclomatic complexity. Warn at 10; error at 15.
  Mirrors the ruff `C901` / `mccabe` defaults.
- `PARAM-001`: function/method parameter count, excluding `self` /
  `cls`. Warn at 5; error at 8.

Skips `__init__.py`, `conftest.py`, `alembic/`, `migrations/`, and
`test_*` files.

---

## Forbidden paths: `PATH-001`

Inert until configured.

Config:
```toml
[tool.lanorme.forbidden_paths]
dirs = ["legacy_src", "build_artifacts"]
```

---

## Layer dependencies: `LAYER-001..005`

For hexagonal / layered codebases with the exact layout `domain/`,
`application/`, `infrastructure/`, `api/`. Inert in their absence.

- `LAYER-001`: `domain/` must not import any other layer.
- `LAYER-002`: `application/` may only import from `domain/`.
- `LAYER-003`: `infrastructure/` may only import from `domain/` and
  `application/`.
- `LAYER-004`: `api/` may only import from `domain/` and
  `application/`.
- `LAYER-005`: only `api/dependencies/` may import from
  `infrastructure/` (the composition root).

These rules track Cockburn's hexagonal architecture and Seemann's
composition-root pattern.

---

## Meta: `META-001..005`

Self-validation that every registered check produces well-formed output.

- `META-001`: non-empty `name`.
- `META-002`: non-empty `description`.
- `META-003`: non-empty `rules` list.
- `META-004`: `CheckResult.check` matches the check's `name`.
- `META-005`: violations carry a non-empty `file`, `rule`, `message`,
  and `fix`.

If you ship a plugin, run `lanorme check . --check=meta` once to confirm
it conforms.

---

## Keyword arguments: `KWARG-001`

Opt-in. With `enabled = true`, every multi-argument function definition
must contain a bare `*` separator to force keyword-only call sites.

Config:
```toml
[tool.lanorme.named_args]
enabled = true
```

---

## Naming conventions: `NAMING-001..004`

- `NAMING-001`: opt-in. Repository methods (files under `repositories/`)
  must use the CRUD prefix set `get_` / `create_` / `update_` / `delete_`
  / `list_`. Conflicts with the DDD ubiquitous-language convention
  (`add`, `of_id`, `for_customer`); off by default.
- `NAMING-002`: opt-in. Service methods under `services/` must use the
  same CRUD prefix set. Conflicts with domain-named operations
  (`approve_loan`, `transfer_funds`); off by default.
- `NAMING-003`: default-on warning. Endpoint handler names should
  match their HTTP verb (`get_user` on `@router.get`, `delete_user` on
  `@router.delete`).
- `NAMING-004`: default-on warning. Functions whose return annotation
  is `bool` should use a boolean prefix (`is_` / `has_` / `can_` /
  `should_`).

Config:
```toml
[tool.lanorme.naming_consistency]
repo_crud    = true   # enable NAMING-001
service_crud = true   # enable NAMING-002
```

---

## Pattern divergence: `IMPORT-001` / `ENDPOINT-001`

- `IMPORT-001`: default-on. Imports must live at the top of the module
  (`import` / `from x import y` statements must not be nested inside a
  function or method body). Equivalent to ruff `PLC0415` with a different
  default.
- `ENDPOINT-001`: default-on warning. Endpoint functions
  (`@router.get` / `@router.post` / ...) must not exceed nesting depth
  4. Deep endpoints correlate with missed branches in auth and
  validation paths.

(`TYPING-001`, no `TYPE_CHECKING` outside model files, was removed
in the audit pass; see CHANGELOG.)

---

## Port coverage: `PORT-001..003`

For hexagonal codebases with `application/ports/`.

- `PORT-001`: every infrastructure service file must import from
  `application/ports/`.
- `PORT-002`: every `Protocol` declared under `application/ports/` must
  have at least one infrastructure implementation. Treat as advisory:
  ports realised only by test doubles or sibling plugins are legitimate.
- `PORT-003`: no direct import or instantiation of an infrastructure
  service from the `api/` layer outside the composition root.

---

## Prose: `PROSE-001..003` on Markdown

Off until enabled.

- `PROSE-001`: em dashes (`-`) in prose.
- `PROSE-002`: American spellings; suggests the British form.
- `PROSE-003`: emoji in prose.

Skips fenced code blocks (` ``` `, `~~~`) and inline `` `code` `` spans.

Config:
```toml
[tool.lanorme.prose]
enabled    = true
extensions = [".md", ".markdown"]   # default
em_dash    = true                   # default
emoji      = true                   # default

[tool.lanorme.prose.spellings]
customize = "customise"             # extend or override the built-in US->UK map
```

---

## Security calls: `SHELL-001` / `DESERIAL-001` / `EVAL-001` / `CRYPTO-001` / `TLS-001` / `DEBUG-001`

All default-on. Single AST walk. Precision-first: when the AST shape is
ambiguous, the rule prefers a false negative over a false positive (no
false sense of security). Use `# noqa: <CODE>` for legitimate uses (e.g.
a pickle load on a trusted local cache) or `[tool.lanorme.per-file-ignores]`
for broader patches.

- `SHELL-001`: `subprocess.run` / `call` / `check_call` /
  `check_output` / `Popen` with `shell=True`; `os.system`; `os.popen`.
- `DESERIAL-001`: `pickle.load(s)`, `marshal.load(s)`, `dill.load(s)`,
  `cPickle.load(s)`, `yaml.load` without `Loader=SafeLoader` /
  `CSafeLoader` / `BaseLoader`, `yaml.unsafe_load`.
- `EVAL-001`: `eval` / `exec` / `compile` where the first argument is
  not a string literal. (Literal-arg `compile(...)` flows are accepted.)
- `CRYPTO-001`: `hashlib.md5` / `hashlib.sha1` used for security
  (`usedforsecurity=False` is honoured), `hashlib.new("md5"/"sha1", ...)`,
  `ssl.PROTOCOL_SSLv2` / `SSLv3` / `TLSv1` / `TLSv1_1`.
- `TLS-001`: `requests` / `httpx` / `aiohttp` call with `verify=False`,
  `ssl._create_unverified_context`, `ssl.CERT_NONE` attribute reference.
- `DEBUG-001`: `Flask(...)` / `FastAPI(...)` constructor with
  `debug=True`, `*.run(debug=True)` / `*.run_server(debug=True)`,
  module-level `DEBUG = True` in `*settings.py` / `*config.py`.

Each rule has a positive + negative unit test under
`tests/unit/test_security_calls.py` locking the AST shape from day one.

---

## Security patterns: `AUTHN-001` / `SQL-001` / `SECRETPY-001`

- `AUTHN-001`: default-on. `@router.post` / `put` / `patch` / `delete`
  handlers must have an auth dependency (a parameter annotated with
  `Depends(get_current_user)` or `Depends(require_*)`). FastAPI-shaped;
  the rule checks for **authentication presence only**, not
  authorisation. Exempt endpoints: `login`, `logout`, `refresh`, `token`.
- `SQL-001`: default-on. AST-based: only flags SQL string literals that
  reach a database execution sink (`.execute` / `.executemany` /
  `.executescript` on a DB-shaped receiver, or `read_sql` /
  `read_sql_query`). Unwraps `text(...)` constructors, resolves module-
  level and function-local string constants, and treats `+` /
  `%`-formatted / `.format`-built SQL as interpolated (always flagged).
  Static SQL passed alongside a `params=` / `parameters=` kwarg (or a
  second positional on `.execute`) with placeholder marks (`:name`,
  `%s`, `?`) is treated as safely parameterised and not flagged.
  Excludes `alembic/` and `test_*` files. Measured against
  `tests/fixtures/security_raw_sql/` (120 labels): **P = 1.000 /
  R = 1.000 / F1 = 1.000**. Known limitations not in the corpus: SQL
  built across multiple statements with helper functions; lazy-loaded
  query templates; non-Python query files.
- `SECRETPY-001`: default-on. Regex-detects hardcoded secrets in
  Python source (`password = "..."`, `api_key = "..."`,
  `aws_access_key_id = "..."`, `Bearer ...`, PEM blocks). Excludes
  `conftest.py`, `seed_dev.py`, `test_*` files, lines with
  `os.environ` / `os.getenv` / `settings.`, empty-string defaults.
  Measured against `tests/fixtures/security_hardcoded_secrets/`:
  **P = 0.758 / R = 0.581 / F1 = 0.658**. Known FP-prone on help-text
  constants and placeholder variants. Known FN-prone on snake-case
  credential names (`aws_access_key`), dict-literal / kwarg-borne
  secrets, and high-entropy keys without a vendor prefix. **Scope
  warning**: Python-source only; `.env`, `*.yaml`, `*.ipynb`, JWTs in
  fixtures are out of scope until a future `SECRET-002` / `SECRET-003`
  lands.

---

## Stale paths: `STALE-001`

Inert until configured. Flags references to old path tokens in
docstrings and comments after a refactor.

Config:
```toml
[tool.lanorme.stale_paths]
tokens = ["src/", "old_pkg/"]
```

---

## Stray artifacts: `JUNK-001/002`

Default-on. Surface tree clutter, including the privacy-relevant cases
of screenshots and editor backups that frequently contain secrets or
PII.

- `JUNK-001`: files matching name patterns: `screenshot*`, `scratch*`,
  `untitled*`, `*~`, `*.bak`, `*.swp`, `*.tmp`, `.DS_Store`,
  `Thumbs.db`, `*.pyc`, `*.pyo`.
- `JUNK-002`: image / binary extensions outside an asset directory.
  Default extensions: `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.webp`.
  Default asset directories: `docs/`, `assets/`, `static/`, `media/`.

Config:
```toml
[tool.lanorme.stray_artifacts]
patterns   = ["*.heic"]            # extra name globs flagged as JUNK-001
extensions = [".zip", ".pdf"]      # extra extensions flagged as JUNK-002
assets     = ["screenshots"]       # extra dirs where binaries are allowed
allow      = ["docs/diagram.png"]  # never flag these (globs)
exclude    = ["sandbox"]           # extra directories to skip entirely
```

---

## Strong types: `TYPE-001..003`

All default-on.

- `TYPE-001`: `dict[str, Any]` (and other weakly-typed dict containers)
  in function signatures or return annotations. Pushes toward DTOs,
  TypedDicts, and value objects.
- `TYPE-002`: bare `dict` / `list` / `tuple` / `set` without type
  parameters. Equivalent in spirit to ruff `UP006`.
- `TYPE-003`: `**kwargs` must be annotated with a concrete type or
  `Unpack[TypedDict]`; bare `**kwargs: Any` is rejected.

---

## Test coverage: `TESTFILE-001`

Default-on warning. For each Python file under a configured production
directory, verify that a `test_*.py` partner exists under `tests/`.
Note this is **file presence**, not coverage; it cannot tell you whether
the test actually exercises the module.

Config (the production directories scanned default to the DDD layout):
```toml
[tool.lanorme.test_coverage]
testable_dirs = [
    ["api/v1/endpoints", "endpoints"],
    ["application/services", "services"],
]
exempt_modules = ["main", "dependencies", "logging"]
```

---

## Test style: `AAA-001` / `AAA-002`

Off until enabled.

- `AAA-001`: test functions with more than `min_statements` (default 3)
  body statements must carry at least `required_markers` (default 2) of
  the AAA section comment markers (`# Arrange`, `# Act`, `# Assert`) or
  their BDD synonyms (`# Given`, `# When`, `# Then`). Setup, exercise,
  call, expect, verify are recognised as additional aliases.
- `AAA-002`: two or more test functions in the same file may not share
  the same `dry_prefix_statements` (default 3) opening statements (the
  arrange block). Extract the shared setup into a pytest fixture or a
  helper.

Config:
```toml
[tool.lanorme.test_style]
enabled               = true
min_statements        = 3
required_markers      = 2     # 1..3
dry_prefix_statements = 3
synonyms              = ["setup", "given", "when", "then"]
```
