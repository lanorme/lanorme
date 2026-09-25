# LaNorme rule reference

This reference describes every rule code LaNorme can emit, one section per code, covering what each rule catches, what it deliberately ignores, where to configure it, and its measured precision, recall, and F1 where a labelled corpus exists.

Corpora live under `evals/corpora/` and scorers under `evals/`.

Live rule list: `lanorme rules`.
Default policy and per-check configuration: see the README.

The rules are grouped by category, roughly in the order `lanorme rules` uses;
`CMT-005..007` and `SECRETPY-001` live in their own checks (`restating`,
`docstrings`, `secrets`). A `-000` code (`TYPE-000`, `DRY-000`, ...) is not a
rule but a notice that a check skipped a file it could not parse, and
`RUN-000` reports a check that raised; both stay warnings whatever `promote`
says.

---

## Attribute access: `ATTR-001` / `ATTR-002`

Opt-in (default-off); both are advisory warnings. Enable with
`[tool.lanorme.attribute_access] enabled = true`. The premise: when an
attribute name is a constant at the call site, the type is known too, so the
dynamic form only hides the attribute from the type checker.

- `ATTR-001`: `hasattr(x, "name")` with a literal identifier name. Branching
  on structure is duck typing; prefer a `runtime_checkable` `Protocol` with
  `isinstance`, or EAFP (`try: ... except AttributeError`).
- `ATTR-002`: `getattr(x, "name")` (no default), `setattr(x, "name", v)`, or
  `delattr(x, "name")` with a literal identifier name. Use direct attribute
  access (`x.name`).

High-confidence cases only. Exempt: three-argument `getattr(x, "name",
default)` (the safe-access idiom); dunder names (`__class__`, `__name__`, ...);
names that are not valid identifiers (cannot be written as `x.name`); a
receiver bound by a plain `import` (`hasattr(os, "fork")`,
`hasattr(socket, "AF_UNIX")`), which is platform feature detection on a
module rather than duck typing of an object; and files under `tests/` or
`test/`. Dynamic names (`getattr(x, name)`) are reflection and exempt unless
`flag_dynamic` is set.

Config:
```toml
[tool.lanorme.attribute_access]
enabled      = true
flag_dynamic = false   # also flag non-literal (reflective) attribute names
```

---

## Comments: `CMT-*` and `PROSE-*` on .py

### `CMT-001`: No commented-out code

Default-on. Walks every `#` comment and parses its text as Python; if the
result is one of `_CODE_NODES` (imports, assigns, defs, control flow,
returns / raises / asserts, ...), the comment is treated as disabled code.
Guards: comments ending in `.` / `?` / `!` are prose; `foo(...)` (literal
`...`) is illustrative; `label: type` with no value is documentation, and
`label: word = value` is a labelled note unless the word is a type
(`# TODO: retries = 5` and `# cython: boundscheck=False` are notes,
`# x: int = 5` is code); an assignment of a foreign literal (`# enabled = true`,
`# timeout = null`) is TOML, YAML or JSON, not Python; a keyword followed only
by an adverb (`# return early`, `# import lazily`, `# raise instead`) is a
prose fragment; the lines that follow a `Usage:`, `Example:` or `e.g.:`
header in the same block show a call shape rather than disable one; tooling
pragmas (`# noqa`, `# type:`, ...) are skipped; and the lines of a
[PEP 723](https://peps.python.org/pep-0723/) `# /// script` ... `# ///` inline
metadata block are tooling, not code.

To recover the shapes `ast.parse` rejects standalone, the comment text is
tried in several wrapping strategies before being declared prose:

- Block headers ending in `:` are tried with a `pass` body.
- `try:` is tried with a `pass` body plus a synthetic `except Exception`.
- `elif` / `else` are tried inside an `if True: pass` prefix.
- `except` / `finally` are tried inside a `try: pass` prefix.
- Bare `return` / `yield` / `raise` are tried inside `def _(): ...`.
- Decorator lines (`@foo`) are tried followed by `def _(): pass`.

Measured against the 187-comment corpus under
`evals/corpora/comments_commented_code/` with `evals/score_cmt001.py`:
**P = 1.000 / R = 1.000 / F1 = 1.000** (TP = 72, FP = 0, FN = 0, TN = 115).
The negatives cover TODO tags, URLs, type contracts, licence headers, shebang
lines, banners, math and units, prose, labelled notes, foreign literals,
adverb fragments, illustrations and pragmas.

Config:
```toml
[tool.lanorme.comments]
commented_code = true   # default-on; set false to disable CMT-001
```

### `CMT-002`: No verbose comments

Default-on. Flags any single comment longer than `max_comment_chars`
(default 120), and any block of consecutive standalone comments longer
than its allowance.

The block allowance is not a constant. A flat cap makes this rule fight
`COMPLEXITY-001`: that rule warns at complexity 10 precisely because such
code is hard to follow, and a six-line cap then forbids explaining why.
So the allowance grows with the complexity of the code the block
introduces:

```
allowance = max_block_lines + (complexity - 1) * block_lines_per_branch
```

At the defaults a trivial helper allows 6 lines and a function at the
`COMPLEXITY-001` warning threshold allows 24. The complexity used is that
of the function the block sits inside, or the one it sits directly above
(within two lines, so a preamble counts). A module-level banner far from
any definition gets the base allowance, and the message names the
complexity it scored so the number is never a mystery.

Set `block_lines_per_branch = 0` for a flat cap.

A preamble above a decorated function counts as sitting above the function:
the decorators do not push it out of reach. Exempt from the block cap: a
block carrying a licence or copyright marker (the Apache preamble is as long
as it is), and a PEP 723 metadata block. Exempt from the line cap: a pragma
line, and the URL part of a line (a URL cannot be wrapped, so only the text
around it is measured).

Config:
```toml
[tool.lanorme.comments]
verbose                = true   # default-on; set false to disable CMT-002
max_comment_chars      = 120
max_block_lines        = 6      # the base, for straight-line code
block_lines_per_branch = 2      # extra lines earned per decision point
```

### `CMT-005`: No comments that restate the next line of code

Default-off. **Experimental.** Lives in its own `restating` check.
Precision-first by design: it only flags a comment when every content word
and verb maps onto the adjacent statement, and an allowlist exempts comments
that carry a why, a caveat, a unit, or a reference. It will miss synonym
paraphrases. Full design: `docs/cmt005-design.md`.

Measured against the 170-comment corpus under
`evals/corpora/comments_restating/` with `evals/score_cmt005.py`:
**P = 1.000 / R = 0.418 / F1 = 0.589** (TP = 33, FP = 0, FN = 46, TN = 91).
The 0.418 recall is bounded by the design's refusal to chase synonym
paraphrases without losing precision.

Config:
```toml
[tool.lanorme.restating]
enabled = true
```

### `CMT-006` / `CMT-007`: docstrings that exist, and that say something

Default-off. **Opinionated.** Lives in its own `docstrings` check.

Every other rule in the comment family subtracts: `CMT-001` deletes
commented-out code, `CMT-002` caps comment length, `CMT-005` deletes
comments that restate the next line, `PROSE-001` / `PROSE-003` strip em
dashes and emoji. The cheapest way to satisfy all of them is to write
nothing. These two point the other way.

- `CMT-006`: a public function or class whose span reaches `min_lines`
  (default 5) carries a docstring. Dunders, private names, members of a
  private class, functions nested inside a function, `test_*` files,
  `__init__.py`, `conftest.py`, `setup.py`, `alembic/` and `migrations/` are
  out of scope. A definition under a module-level `if` or `try` is in scope.
- `CMT-007`: that docstring says more than the signature. A docstring is
  vacuous when every content word in it is already carried by the
  definition's name, its parameters, or its enclosing class. Padding does
  not help, because padding is restatement, and a placeholder
  (`"""TODO"""`, `"""Docstring."""`) is filler.

`CMT-007` reuses the vocabulary machinery behind `CMT-005`: identifier
splitting, stemming, and the 11-category allowlist that exempts a comment
carrying a why, a caveat, a unit or a reference. Two additions on top:

- **Abbreviation coverage.** A stemmer links `process` to `processing` but
  not to `proc`, so a docstring word also counts as restatement when it
  extends a signature stem of at least 3 characters, or is extended by
  one. The floor stops a two-letter name such as `go` swallowing `govern`.
- **Emptiness before allowlist.** A docstring with no content word left
  after filler removal is vacuous whatever the allowlist says, so
  `This is a helper function.` is not rescued by it.

Measured against the 29-definition corpus under
`evals/corpora/docstrings_vacuous/` with `evals/score_cmt007.py`:
**P = 1.000 / R = 1.000 / F1 = 1.000** (TP = 11, FP = 0, FN = 0, TN = 18).
That corpus was written alongside the rule and tuned against, so treat the
figure as a regression guard rather than an unbiased estimate. The
independent evidence is held-out: `CMT-007` returns **zero** findings over
the 18 generated modules (about 10,000 lines) in `evals/readability/runs/`
and `evals/articulacy/runs*/`, and zero over LaNorme's own `src/` with
`require_private` on, while `CMT-006` finds 114 missing docstrings in the
same generated corpora.

`CMT-007` is a guard, not a finder. Its job is to stop `CMT-006` being
satisfied by `"""Go."""`; on code written in good faith it should stay
silent, and on this evidence it does.

Config:
```toml
[tool.lanorme.docstrings]
enabled         = true
min_lines       = 5      # definitions shorter than this need no docstring
require_private = false  # also require them on _private definitions
```

### `PROSE-001` / `PROSE-003` on comments and docstrings

Off until enabled. The same rule codes that the `prose` check emits on
Markdown also fire here, on `#` comments and `"""..."""` docstrings,
when configured. An emoji is a code point with Unicode's Emoji property, or
any symbol followed by the emoji presentation selector (U+FE0F); a check mark
(U+2713), a ballot box, a star, a die or a musical note is not one, and
neither is the zero-width joiner that Hindi or Arabic text carries.

Config:
```toml
[tool.lanorme.comments]
em_dash = true   # emit PROSE-001 on comments/docstrings
emoji   = true   # emit PROSE-003 on comments/docstrings
```

---

## Docs: `DOCS-001..008`

Opt-in (default-off), tree-scoped. Enforces a familiar Diataxis-style structure
and accessibility on a Markdown documentation tree. Headings and images inside
a fenced code block, YAML front matter or an inline code span are never
matched, and a rule (`---`) under a list item, a quote or a table row is a
thematic break, not a setext heading. It inspects only Markdown
under `docs_root` (default `docs`); files anywhere else are ignored, so it never
imposes a docs structure on source trees or stray Markdown. Headings and images
inside fenced code blocks are never matched, so a sample showing a hash heading
or an image link is left alone. `DOCS-001..004` are build-failing errors;
`DOCS-005..008` are advisory warnings.

- `DOCS-001`: a content page has exactly one level-1 heading.
- `DOCS-002`: heading levels descend one step at a time (no skipped levels).
- `DOCS-003`: a content page opens with a canonical skimmer line. The first
  prose line after the H1 must begin with one of `This page`, `This tutorial`,
  `This guide`, `This reference`, `This how-to`, or `This explanation`.
- `DOCS-004`: every image carries non-empty alternative text, for both
  Markdown image links and HTML `img` tags.
- `DOCS-005`: prefer SVG or Mermaid over a local raster image (warning).
- `DOCS-006`: a known section directory that has pages carries an `index.md`
  landing page (warning).
- `DOCS-007`: every page lives in a known section or is a known top-level
  page (warning).
- `DOCS-008`: headings are not numbered by hand; renderers number sections
  for you (warning).

Vendored or generated directories (`.git`, `.venv`, `node_modules`,
`__pycache__`, `dist`, `build`, and so on) are never treated as part of a docs
tree.

Config (all keys optional; the defaults are shown):
```toml
[tool.lanorme.docs]
enabled   = true                  # default false (the whole check is opt-in)
docs_root = "docs"                # tree to inspect, relative to the scan target
# Diataxis section directories expected under docs_root.
sections  = ["tutorials", "how-to", "reference", "explanation"]
# Pages allowed at the top level without a section home (DOCS-007).
known_top_level = [
  "index.md",
  "RULES.md",
  "reference/configuration.md",
  "reference/rules-index.md",
  "reference/cli.md",
]
# Image extensions treated as raster formats for DOCS-005.
raster_extensions = ["png", "jpg", "jpeg", "gif", "webp", "bmp"]
allow = ["**/logo.png"]           # globs exempt from the prefer-SVG warning (default empty)
```

The check is tree-scoped (`scope = "tree"`): it reads the directory layout, not
only individual files, so the section-index and quadrant rules can reason about
where each page sits.

---

## Domain terminology: `TERM-NNN`

ConfigurableCheck ubiquitous-language enforcement. Inert by default. Each
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

Default-on. Detects **exact structural clones**: functions with an identical
normalised AST body and at least five statements (a leading docstring is
documentation and counts for neither). Normalisation strips variable names and
string literals, so two functions differing only in identifier spelling or
string-constant content still match. The name a call targets is kept, like an
attribute name: `min(...)` against `max(...)`, or `any(...)` against
`all(...)`, is a different operation, not a renamed variable. It is precise but
strict: a single added statement, a reordering, a changed number, a renamed
attribute or a renamed call defeats the match. For the fuzzier "these should
share a helper" cases, see `SIMILAR-001` below.

Config: none. False positives on intentionally parallel
adapters across bounded contexts are a known limit; suppress them with
`[tool.lanorme.per-file-ignores]`, `# noqa: DRY-001`, or
`# lanorme: ignore[DRY-001]`.

---

## Near-duplicate: `SIMILAR-001`

Opt-in (default-off), advisory **warning** (never fails the build). The fuzzy
companion to `DRY-001`: it catches near-duplicates that the exact check misses
(one or two added statements, reordering, a changed number, a renamed
attribute, a renamed call) so a reviewer can decide whether to extract a shared
helper.

Two functions in a file are compared on two signals. **Structure**: a token
sequence over the body that abstracts away variable names, attribute names and
numbers, scored with `difflib` (so a one-statement or reorder drift still
aligns). **Anchors**: the meaning-bearing tokens `DRY-001` discards: string
literals, called names, operator kinds, and accessed attribute names, each
scored by weighted Jaccard. A pair flags only when the structure is similar
**and** every anchor agrees, which keeps precision high: parallel boilerplate
that shares a shape but differs in its string keys or source attributes (config
builders, dispatch tables, field mappers, framework handlers) is rejected.
Equality/dunder/`@property` boilerplate and drifted logging-message strings are
handled specially. Measured on the bundled corpus
(`evals/corpora/duplication_similar/`, scorer `evals/score_similar.py`):
**precision 1.000 / recall 0.850 / F1 0.919**. Known recall gaps: fully renamed
attribute sets and error-message-only drift.

```toml
[tool.lanorme.similarity]
enabled = true
# threshold overrides (defaults shown):
min_statements = 5
struct_ratio   = 0.55
str_jaccard    = 0.60
op_jaccard     = 0.60
call_jaccard   = 0.35
attr_jaccard   = 0.10
```

---

## File limits: `SIZE-*` / `COMPLEXITY-001` / `PARAM-001`

All default-on.

- `SIZE-001`: Python files. Warn at 300 effective (non-blank,
  non-comment) lines; error at 500.
- `SIZE-002`: functions and methods. Warn at 50 lines; error at 80. The
  docstring is left out: it documents the function rather than lengthening
  it.
- `SIZE-003`: classes with more than 10 methods (warning only). Useful
  as a smell on services and views; on rich aggregate roots in a DDD
  codebase, expect to silence it via `per-file-ignores`.
- `COMPLEXITY-001`: cyclomatic complexity. Warn at 10; error at 15 (the
  ruff `C901` / `mccabe` default thresholds). Complexity is 1 plus one for
  each decision point: an `if` / `elif` / `for` / `while` / `except` /
  `with` / `assert` / ternary, each extra `and` / `or` operand, each
  **refutable `match` case** (an irrefutable catch-all such as `case _:` or
  a bare `case x:` does not count, like an `else`), and, inside a
  comprehension, each **filter `if`** and each **nested `for`** clause. One
  deliberate divergence from a textbook count: a comprehension's *primary*
  `for` is treated as a single expression and does not count, so a plain
  `[f(x) for x in xs]` costs nothing. That keeps the 10 / 15 thresholds
  calibrated for code that leans on comprehensions; the conditional and
  nested-loop branching they can hide still counts.
- `PARAM-001`: function/method parameter count, excluding the receiver
  (`self` / `cls`, or `mcs` / `metacls` in a metaclass). Warn at 5; error
  at 8.

Skips `__init__.py`, `conftest.py`, `alembic/`, `migrations/`, and
`test_*` files.

Every threshold above is a default, not a fixed number. A project sets its
own without giving up the rule:

```toml
[tool.lanorme.file_limits]
file_warn_lines = 400
file_error_lines = 600
func_warn_lines = 60
func_error_lines = 100
class_method_warn = 15
complexity_warn = 12
complexity_error = 20
param_warn = 6
param_error = 10
```

Each key is optional and an unset key keeps its default, so a project
declares only what differs. There is no `enabled` key: these retune the
limits rather than switch the rules off. A warn threshold set above its
error threshold describes no reachable band, so the error value wins for
both and everything at the limit reports an error rather than a warning.

Under cascading config a nested `lanorme.toml` sets its own limits for the
files below it, which is how a legacy subtree keeps a looser ceiling while
the rest of the repository holds the strict one.

The rule strings carry no number (`SIZE-001: File exceeds the effective
line limit`, not `... exceeds 500 effective lines`), so retuning a
threshold does not move a finding's baseline anchor. The number a finding
was measured against appears in its message.

---

## Forbidden paths: `PATH-001`

Inert until configured.

Config:
```toml
[tool.lanorme.forbidden_paths]
dirs = ["legacy_src", "build_artifacts"]
```

---

## Layer dependencies: `LAYER-001..007`

For hexagonal / layered codebases with a `domain/`, `application/`,
`infrastructure/`, `api/` layout. Inert in their absence.

If the layers live under a nested package directory, set the top-level
`[tool.lanorme] source_root` (e.g. `"src/myproject"`) so they are classified
relative to it. Files outside `source_root` are layer-exempt;
`composition_root` is then read relative to `source_root` too. Reported paths
stay relative to the scan target.

- `LAYER-001`: `domain/` must not import any other layer.
- `LAYER-002`: `application/` may only import from `domain/`.
- `LAYER-003`: `infrastructure/` may only import from `domain/` and
  `application/`.
- `LAYER-004`: `api/` may only import from `domain/` and
  `application/`.
- `LAYER-005`: only the composition root may import from
  `infrastructure/`.
- `LAYER-006`: a `transport_layers` entry is not among the configured
  `layers`, so it has no effect (advisory **warning**, exit 0).
- `LAYER-007`: a layer added through `layers` imports a layer its `allowed`
  entry does not list. The fix names the layers it may import.

These rules track Cockburn's hexagonal architecture and Seemann's
composition-root pattern.

A relative import is resolved against the importing file's package first, so
`from .application import X` inside `domain/` names a sibling module in the
domain layer, not the application layer, while `from ..infrastructure import db`
still reaches the infrastructure layer.

The composition-root exception applies to any layer listed in
`transport_layers` (default `["api"]`). Apps with several peer transport
adapters (a REST `api/`, an `mcp_server/`, a `grpc_server/`) can list them all
so each keeps its own composition root. A transport peer must also appear in
`layers` and be given an `allowed` entry.

Config (all keys optional; the defaults are shown):

```toml
[tool.lanorme.layer_deps]
# Files allowed to import infrastructure (the composition root).
# fnmatch globs against the source-relative path, so a module FILE
# (api/dependencies.py) is recognised, not only a directory.
composition_root = [
  "api/dependencies/**", "api/dependencies.py", "api/deps.py",
  "api/v1/dependencies/**", "api/v1/dependencies.py", "api/v1/deps.py",
  "api/v1/main.py",
]

# For layouts whose layers differ. Defaults shown.
layers = ["domain", "application", "infrastructure", "api"]

# Transport (inbound adapter) layers eligible for the composition-root
# exception. A peer must also appear in layers and get an allowed entry.
transport_layers = ["api"]
[tool.lanorme.layer_deps.allowed]
application    = ["domain"]
infrastructure = ["domain", "application"]
api            = ["domain", "application"]
```

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
must contain a bare `*` separator to force keyword-only call sites. Dunder
methods and methods decorated `@override` are exempt: their signature is fixed
by the protocol or the base class, so the finding belongs there.

Config:
```toml
[tool.lanorme.named_args]
enabled = true
```

---

## Naming canon: `NAMING-006..008`

Default-on warnings. Lives in the `naming_canon` check.

Three rules, each stating a point the naming canon agrees on across
languages and schools. The sources are the ones the rules were read from:

- Robert C. Martin, *Clean Code*, chapter 2, "Meaningful Names": classes and
  objects take noun or noun-phrase names and a class name should not be a
  verb; methods take verb or verb-phrase names.
- Steve McConnell, *Code Complete*, 2nd edition, section 7.3, "Good Routine
  Names": name a procedure with a strong verb followed by an object, name a
  function for the value it returns, and avoid vague verbs such as
  `HandleCalculation`, `PerformServices`, `ProcessInput` and
  `DealWithOutput`.
- Brian Kernighan and Rob Pike, *The Practice of Programming*, section 1.1:
  use active names for functions.
- The [Java Code Conventions](https://www.oracle.com/java/technologies/javase/codeconventions-namingconventions.html),
  the .NET [names of classes](https://learn.microsoft.com/dotnet/standard/design-guidelines/names-of-classes-structs-and-interfaces)
  and [names of members](https://learn.microsoft.com/dotnet/standard/design-guidelines/names-of-type-members)
  guidelines, and the [Kotlin coding conventions](https://kotlinlang.org/docs/coding-conventions.html#choose-good-names):
  classes are nouns or noun phrases, methods are verbs or verb phrases.
- The [Swift API Design Guidelines](https://www.swift.org/documentation/api-design-guidelines/):
  name functions by their side effects, an imperative verb phrase when they
  have them and a noun phrase when they do not.
- [Effective Go](https://go.dev/doc/effective_go#Getters) and the
  [Rust API Guidelines](https://rust-lang.github.io/api-guidelines/naming.html):
  a getter carries no `Get` prefix. Bertrand Meyer, *Object-Oriented
  Software Construction*, and the *Ada 95 Quality and Style Guide*, section
  3.2.3, state the same split as command-query separation: verbs for
  procedures, nouns for value-returning functions, predicates for boolean
  ones.
- [PEP 8](https://peps.python.org/pep-0008/#naming-conventions) is silent on
  parts of speech, and Python's own library follows the second school:
  `len`, `basename`, `Path.cwd()` and every property are queries named for
  their value.

Where the schools split, on whether a pure query must also lead with a verb,
these rules stay out; that stricter reading is the opt-in
`naming_clean_code` check below. Every finding here is a warning. A
project that wants the canon as a hard standard promotes the codes, as
LaNorme does on itself.

### `NAMING-006`: A class is named as a thing, not as an action

A class whose first word is unambiguously a verb and whose last word is the
verb's object reads as an imperative sentence: `FetchUsers`, `SendEmail`,
`ValidateOrder`, `CalculateTax`. A class is a thing, so the fix is to name
the thing, usually the agent (`OrderValidator`, `EmailSender`) or what it
holds.

Precision comes from two guards. The first word must be on a short
verb-only list: `Build`, `Check`, `Run`, `Update`, `Process`, `Load`,
`Test`, `Compute` and `Render` are nouns as often as verbs (`BuildResult`,
`ProcessPool`, `TestUser`, `ComputeNode`), so they never open a finding, and
`ProcessPayment` or `UpdateUser` is a known miss. And the last word must not
be an action's own attribute or artefact: `FetchOptions` holds options,
`ConnectTimeout` is a timeout, `CompileError` is an error, `DeleteView` is a
view, `LoadBalancer` is an agent noun. Words a verb acts on (`User`,
`Order`, `Connection`, `Session`, `Token`) are deliberately not on that
list, or `CreateUser` would pass.

A class with an exception base (one named `...Error`, `...Exception`,
`...Warning`, `...Exit` or `...Interrupt`) is a thing whatever it is
called, and a last word that names the state an action reached
(`SendFailed`, `FetchAborted`, `LoadComplete`) names an outcome, so both
pass. A few attribute heads are listed by word (`behaviour`, `direction`,
`reason`, `timing`, `wizard`, `impl`, `abc`), and a head that is a verb's
object as often as its attribute (`Permission`, `Scope`, `Metrics`,
`Progress`) is deliberately not, so `EmitMetrics` and `CreatePermission`
stay reported; a project that names enums that way adds them to `exempt`.

A message object is a noun by convention and is exempt through its suffix:
`Command`, `Query`, `Event`, `Handler`, `UseCase`, `Request`, `Response`,
`Job`, `Task`, extensible through `command_suffixes`. A verb-first class
without one, such as SQLAlchemy's DDL objects `CreateTable` and
`AddConstraint`, is reported; a project that models statements that way
adds them to `exempt`. Names that are not PascalCase (a ctypes structure
such as `CONSOLE_SCREEN_BUFFER_INFO`) and classes defined inside a function
are not judged.

### `NAMING-007`: A function that does something is named verb-first

A function whose body returns no value (no `return x`, no `yield`) exists
for its effect, so its name says what it does, verb first. The finding is a
function like `layout(root)` that writes files, `cert_verify(conn)` that
sets connection options, or `versioned_session(session)` that attaches a
listener. When a verb sits later in the name the fix says where to move it
(`verify_cert`); otherwise it asks for one.

What is left alone, because the name was not the author's to choose or the
function is not a command:

- A function that returns a value or yields, including an explicit
  `return None`: a query may be named for its value.
- A body that is only a docstring, `pass` or `...` (a stub or a protocol
  member), or that ends in `raise` (a raiser such as `key_not_found`).
- A function defined inside another function: closures such as `wrapper`
  and the hooks a test registers inline are local.
- A function under any decorator other than `staticmethod`, `classmethod`,
  `abstractmethod`, `override` or `final`: a route, a fixture, a property
  setter, a signal receiver or a CLI command is named by the framework's
  contract.
- Dunders, keyword-clash names ending in `_`, hook names matched by word
  so camelCase and bare forms count (`on_click`, `onMessage`, `pre_save`,
  `after_request`, `pytest_configure`; `error_handler`, `_dynamic_class_hook`,
  a bare `callback`, `handler` or `listener` handed to `signal.signal` or
  `apply_async`), conversions and constructors (`from_`, `to_`, `as_`,
  `into_`, `with_`, and `x_to_y`), entry points (`main`, `async_main`,
  `cli`, the WSGI `application` of PEP 3333 and the ASGI `app`),
  standard-library protocol methods on a class (`keys`, `fetchone`,
  `rollback`, `flush`, `info`; the `asyncio` protocol hooks
  `connection_made` and `data_received`, `socketserver`'s `server_bind`,
  `ast.NodeVisitor.generic_visit`, `xml.sax`'s `characters`, `cmd.Cmd`'s
  `emptyline`, `io`'s `readinto`, `urllib`'s `http_open`), the Django and
  Scrapy hook names on plain classes (`process_request`, `process_item`),
  and on a method only, the names Django, Django REST framework, Scrapy,
  pydantic and SQLAlchemy fix (`ready`, `form_valid`, `allow_migrate`,
  `test_func`, `perform_create`, `closed`, `spider_opened`,
  `model_post_init`, `column_expression`): a module-level `ready()` is the
  author's. `setUp` and `tearDown` pass because `set` and `tear` are verbs.
- A camelCase method on a class with bases (`mousePressEvent`,
  `dataReceived`, `onMessage`): PEP 8 allows mixedCase only where it is
  the prevailing style, so the name is the base API's. The same name on a
  class without bases, or at module level, is judged.
- Files under `migrations/` or `alembic/` inside the scanned tree, whose
  names a tool generated. A name with non-ASCII letters is not judged.

The verb test is recall-first: a word counts as a verb if it is listed, is
a third-person form (`matches`), carries a verb suffix (`simplify`,
`normalise`), sits behind a fused prefix (`reload`, `unquote`,
`deregister`, `aclose`) or opens with one of the verbs Python fuses onto
the next word (`getheaders`, `setdefault`, `isdigit`, `iteritems`; not
`password`, `endpoint` or `checksum`), and leading modifiers are skipped
(`bulk_insert`, `re_apply`, `atomic_write`, `safe_delete`). The listed
verbs include the business, moderation and security verbs a domain model
uses (`refund`, `invite`, `enrol`, `ban`, `redact`, `mint`), and a noun
that merely ends like a verb (`enterprise`, `premise`, `chunksize`) is not
one. A word wrongly counted as a verb hides a finding and never creates
one. The `verbs` setting extends the vocabulary, and a configured weak verb
counts as a verb here too.

### `NAMING-008`: A function does not open with a weak verb

`handle_`, `process_`, `perform_`, `do_`, `manage_` and `deal_with_` say
that something happens to the object without saying what. Code Complete
lists them as the verbs to avoid, and `do_` is the Python spelling of the
same evasion. The finding is a function such as `handle_data` or
`process_order` whose body parses, stores, validates or prices; the fix is
to say which.

A bare `handle` or `process` with no object is a dispatcher's slot and is
not reported. A method on a class with bases may be overriding an inherited
name (`do_GET` on a request handler, `process` on a SQLAlchemy type), so
it is not reported either; nor is any name `NAMING-007` leaves alone as
not the author's to choose, which covers Django REST framework's
`perform_create` and Django's `handle_label` on a plain mixin. The rule
applies to queries as well as commands. The list is replaced, not
extended, through `weak_verbs`.

Config:
```toml
[tool.lanorme.naming_canon]
verbs            = ["frob"]              # words that read as verbs in this codebase
command_suffixes = ["Interactor"]        # extends the message-object suffixes (NAMING-006)
weak_verbs       = ["handle", "process"] # replaces the default list (NAMING-008)
exempt           = ["CreateTable"]       # names no rule here judges, with or without leading underscores
```

Measured on the third-party code under `benchmarks/.corpora/` (Flask,
requests, rich and SQLAlchemy: 879 files, about 620k lines): `NAMING-006`
reports 15 classes, of which SQLAlchemy's `DeleteAll`, `SaveUpdateAll`,
`RemoveORMEventsGlobally` and six DDL statement objects are the bulk;
`NAMING-007` reports 220 functions, 101 of them with the verb elsewhere in
the name; `NAMING-008` reports 24. LaNorme's own source and tests are
clean under all three, with the codes promoted to errors.

Measured against the labelled corpora with `evals/score_naming006.py`,
`evals/score_naming007.py` and `evals/score_naming008.py`:

| rule | corpus | P | R | F1 | TP | FP | FN | TN |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `NAMING-006` | `naming_verb_class` | 1.000 | 1.000 | 1.000 | 8 | 0 | 0 | 18 |
| `NAMING-007` | `naming_command` | 1.000 | 1.000 | 1.000 | 11 | 0 | 0 | 39 |
| `NAMING-008` | `naming_weak_verb` | 1.000 | 1.000 | 1.000 | 8 | 0 | 0 | 14 |

Those corpora are regression guards for the exemptions listed above, not
an unbiased estimate; the calibration evidence is the third-party
measurement.

---

## Naming, Clean Code: `NAMING-009..011`

Default-off. **Opinionated.** Lives in its own `naming_clean_code` check.

Chapter 2 of *Clean Code* goes past the canon above in two places, and this
check enforces both, plus the module-level version of one of them.

### `NAMING-009`: A class name carries no noise word

Clean Code names the words to keep off a class: `Manager`, `Processor`,
`Data` and `Info` name a job title or a shrug where a thing should be.
`Helper`, `Util` and `Utils` join them. The finding is `ClassManager`,
`DependencyProcessor`, `ConfigData` or `ScriptInfo`; the fix is to say what
the thing is (a registry, a pool, a scheduler) or what it holds. A single
word (`Manager` as a domain class), `MetaData` and its compounds, a
`ContextManager`, a class whose base already ends in the word
(`UserManager(models.Manager)` is a Django manager, `ProcessManager` a
`multiprocessing` one) and names that are not PascalCase are not reported.

### `NAMING-010`: A module is not a junk drawer

`utils`, `util`, `utilities`, `helpers`, `helper`, `common`, `misc` and
`stuff` promise nothing about what is inside; the
[Go package-naming advice](https://go.dev/blog/package-names) says so in as
many words. A module or a package (an `__init__.py` in a directory of that
name) is reported on line 0; the fix is to split it by responsibility and
name each part for what it holds. Flask, requests and SQLAlchemy each carry
at least one, which is why the rule is opt-in.

### `NAMING-011`: Every function starts with a verb

The Java-school reading: a query leads with a verb too, so
`_shell_violations()` becomes `find_shell_violations()` and `url_for()`
becomes `build_url()`. Commands are `NAMING-007`'s and a raiser exists to
raise, so this rule takes the rest: functions that return a value, and
stubs. Predicates that carry an auxiliary anywhere (`is_valid`,
`line_has_noqa`) or open with a third-person verb (`exists`, `matches`),
constructors under `@classmethod`, properties, conversions, a decorator or
closure factory (a function that returns a function it defines, or a
lambda: `deprecated`, `cached` are named for what they confer) and every
reserved-name shape `NAMING-007` lists are exempt, and the same verb
vocabulary applies. A decorator returned through a variable
(`actual = user_passes_test(...); return actual`) is not recognised as one.

This is a house choice, not a correction. Naming a pure function for its
value is the other canonical school (Code Complete, Ada, Swift, Kotlin, Go,
Rust and Python's own library), and it is the one LaNorme's source follows:
on LaNorme's `src/` it reports every helper named for the value it
returns, well over a hundred of them. A project that opts in is choosing
the Java-school reading for itself.

Config:
```toml
[tool.lanorme.naming_clean_code]
enabled = true
verbs   = ["frob"]       # words that read as verbs in this codebase
exempt  = ["url_for"]    # names no rule here judges
```

Measured on the third-party code under `benchmarks/.corpora/`:
`NAMING-009` reports 10 classes, `NAMING-010` 9 modules, `NAMING-011`
1473 functions. Measured against `evals/corpora/naming_every_verb/` with
`evals/score_naming011.py`: **P = 1.000 / R = 1.000 / F1 = 1.000**
(TP = 10, FP = 0, FN = 0, TN = 23), a regression guard for the exemptions
rather than an unbiased estimate. `NAMING-009` and `NAMING-010` match exact
words and have unit tests instead of a corpus.

---

## Naming conventions: `NAMING-001..004`

- `NAMING-001`: opt-in. Repository methods (files under
  `infrastructure/repositories/` or `infrastructure/persistence/`) that use
  a non-canonical synonym prefix (`fetch_` / `retrieve_` / `find_` /
  `remove_` / `add_`) are flagged and steered to the CRUD equivalent
  (`get_` / `create_` / `update_` / `delete_` / `list_`). Conflicts with the
  DDD ubiquitous-language convention; off by default.
- `NAMING-002`: opt-in. Service methods (files under `application/services/`)
  that use the same synonym prefixes are flagged and steered to the CRUD
  equivalent. Conflicts with domain-named operations (`approve_loan`,
  `transfer_funds`); off by default.
- `NAMING-003`: default-on warning. Endpoint handler names (in files
  under `api/v1/endpoints/`) should match their HTTP verb (`get_user` on
  `@router.get`, `delete_user` on `@router.delete`). Health probes and
  auth-issuance handlers are exempt.
- `NAMING-004`: default-on warning. Functions whose return annotation
  is `bool` should read as an assertion: a boolean prefix (`is_` / `has_` /
  `can_` / `should_`), an auxiliary anywhere (`does_match`, `user_is_active`),
  a third-person verb in front (`exists`, `matches`, `contains`,
  `needs_refresh`) or a fused `isdir` / `hasattr` / `isEnabled` all pass;
  `check_password` and `valid` are reported. Private names, `Protocol`
  members, properties and the names a protocol or framework fixes
  (`readable`, `filter` on a `logging.Filter`, Django's `allow_migrate`,
  Qt's `eventFilter` on a subclass) are not judged.

Config:
```toml
[tool.lanorme.naming_consistency]
repo_crud    = true   # enable NAMING-001
service_crud = true   # enable NAMING-002
```

---

## Naming scope: `NAMING-005`

Default-off. **Opinionated.** Lives in its own `naming_scope` check.

`NAMING-005` does not ban short names. `i` in a three-line loop is
perfectly readable; the same `i` bound at the top of a sixty-line function
and used at the bottom is not, because the binding has scrolled out of
sight and the name itself has to carry the meaning. The defect is
shortness *held over distance*, so the requirement scales with the span.

Span is measured from where a name is first bound to where it is last
referenced, within one function. Only names the function itself binds are
considered (parameters, assignment targets and `match` captures): a
referenced-but-unbound short name such as `np` or `re` is an imported
module, where the name is the library's choice and not the function's. A
name a comprehension, a lambda or a nested function binds belongs to that
scope, so an `r` in a comprehension at the top and another in a lambda at
the bottom are two names, not one carried across; a use of the outer
name inside such a scope still counts.

Choosing the default (`max_span = 20`), measured over LaNorme's own
`src/` and the 18 generated modules (about 10,000 lines) under `evals/`:

| corpus | p90 span | p95 | max | findings at 20 |
| --- | --- | --- | --- | --- |
| `src/` | 7 | 10 | 18 | 0 |
| generated code | 21 | 24 | 53 | 9 |

The default sits in that gap, and has a reason beyond the gap: twenty
lines is roughly a screenful, the point at which a binding and its use
stop being visible together. At that default LaNorme's own source and test
suite are clean, and the generated corpus yields `rc` held over 53 lines,
`s` over 52 and `p` over 50.

Short names that stay readable at any distance are exempt by default:
`_`, `i`, `j`, `k`, `n`, `x`, `y`, `z`, `db`, `id`, `fd`, `fh`, `ok`,
`lo`, `hi`, `lr`, `ax`, `df`, `ts`. Extend the list rather than raising the
span, so the exemption stays visible in config. Numeric and ML code is the
usual reason to: fitted parameters such as `a` / `b` / `c` read fine to
their audience and are the rule's most likely false positive.

Config:
```toml
[tool.lanorme.naming_scope]
enabled          = true
max_span         = 20        # lines between binding and last use
max_short_length = 2         # names this long or shorter are "short"
allow            = ["mu"]    # extends the default allowlist
```

Measured against `evals/corpora/naming_scope/` with
`evals/score_naming005.py`: **P = 1.000 / R = 1.000 / F1 = 1.000**
(TP = 3, FP = 0, FN = 0, TN = 6). That corpus is a regression guard, not
an unbiased estimate; the calibration evidence is the table above.

---

## Pattern divergence: `IMPORT-001` / `ENDPOINT-001`

- `IMPORT-001`: default-on. Imports must live at the top of the module
  (`import` / `from x import y` statements must not be nested inside a
  function or method body). Equivalent to ruff `PLC0415` with a different
  default. Imports inside an `if TYPE_CHECKING:` guard are exempt, as are
  files under `infrastructure/observability/` and `api/v1/main.py`
  (conditional startup wiring); `test_*` files are skipped.
- `ENDPOINT-001`: default-on warning. Functions defined in files under
  `api/v1/endpoints/` must not exceed nesting depth 4. Deep endpoints
  correlate with missed branches in auth and validation paths.

---

## Port coverage: `PORT-001..003`

For hexagonal codebases with `application/ports/`. As with `layer_deps`, the
top-level `[tool.lanorme] source_root` anchors `ports_dir`, `adapter_roots`,
and `composition_root` under a nested package directory when set.

- `PORT-001`: every adapter file (under the adapter roots) must import
  from the ports directory. A private module (`_retry.py`) is a utility by
  convention and is not asked to.
- `PORT-002`: every `Protocol` declared in the ports directory must
  have at least one implementation. Build-failing like `PORT-001` and
  `PORT-003`. Ports realised only by test doubles or sibling plugins are
  legitimate; list them in `ports_without_impl` or ignore the code.
- `PORT-003`: no direct import or instantiation of an infrastructure
  adapter from the `api/` layer outside the composition root.

Config (all keys optional; the defaults are shown):

```toml
[tool.lanorme.port_coverage]
ports_dir        = "application/ports"     # where port Protocols live
adapter_roots    = ["infrastructure/services"]  # dirs scanned for adapters (recursive)
composition_root = ["*dependencies/*", "*dependencies.py", "*deps.py", "*v1/main.py"]  # PORT-003 exemption (globs)
skip_files         = ["__init__.py"]
ports_without_impl = ["repositories.py", "unit_of_work.py", "otel.py", "metrics.py"]
```

Adapter roots are scanned recursively, so widening `adapter_roots` to
`["infrastructure"]` picks up adapters in per-integration subdirectories.

---

## Prose: `PROSE-001..004` on Markdown

Off until enabled.

- `PROSE-001`: em dashes (U+2014) in prose.
- `PROSE-002`: American spellings; suggests the British form.
- `PROSE-003`: emoji in prose (Unicode's Emoji property, or a symbol carrying
  the emoji presentation selector; a check mark or a star is not one).

Skips fenced code blocks (` ``` `, `~~~`; a fence closes only on a run at
least as long as the one that opened it), YAML front matter, inline
`` `code` `` spans, link targets, URLs and HTML tags. `PROSE-002` also
ignores tokens that are code rather than words: a flag (`--color`), a dotted
name (`settings.color`), a path, an assignment, or anything holding a digit
or an underscore (`org-color-42`).

### `PROSE-004`: Em-dash density above natural English

Advisory warning, opt-in (default-off). Where `PROSE-001` bans every em dash
outright, `PROSE-004` instead measures how often em dashes appear and warns once
per file when the density runs above what natural English sustains. It reuses
the same code-span and fenced-block stripping as the other prose rules, so it
measures the remaining prose alone.

The heuristic has an eligibility floor and two fire thresholds. A file is only
measured when it clears all three floor values (`min_em` em dashes, `min_words`
words, `min_sentences` sentences), which keeps the rule silent on anything too
short to judge. It then fires only when **both** density thresholds are
exceeded: em dashes per 1000 words above `em_per_1000` **and** the fraction of
sentences carrying an em dash above `em_sentence_fraction`. ANDing the two axes
stops a single high reading from tripping the warning. Setting `em_dash = false`
while `em_dash_density = true` switches a region from the `PROSE-001` ban to
this density advisory.

Measured on the labelled corpus `evals/corpora/prose_em_dash` (run
`evals/score_prose004.py`): **P = 1.000 / R = 1.000 / F1 = 1.000** (TP = 3,
FP = 0, FN = 0, TN = 11). The corpus exists to prove the calibrated thresholds
do not false-positive on natural prose.

Config:
```toml
[tool.lanorme.prose]
enabled         = true
extensions      = [".md", ".markdown"]   # default
em_dash         = true                   # default; PROSE-001 ban
emoji           = true                   # default
em_dash_density = true                   # enable PROSE-004 (default false)

[tool.lanorme.prose.spellings]
customize = "customise"             # extend or override the built-in US->UK map

[tool.lanorme.prose.density]
min_em               = 4      # eligibility floor: minimum em dashes
min_words            = 400    # eligibility floor: minimum words
min_sentences        = 30     # eligibility floor: minimum sentences
em_per_1000          = 30.0   # fire threshold: em dashes per 1000 words
em_sentence_fraction = 0.50   # fire threshold: fraction of sentences with an em dash
```

---

## Security calls: `SHELL-001` / `DESERIAL-001` / `EVAL-001` / `CRYPTO-001` / `TLS-001` / `DEBUG-001`

All default-on. Single AST walk. Precision-first: when the AST shape is
ambiguous, the rule prefers a false negative over a false positive (no
false sense of security). Use `# noqa: <CODE>` or `# lanorme: ignore[<CODE>]`
for legitimate uses (e.g. a pickle load on a trusted local cache) or
`[tool.lanorme.per-file-ignores]` for broader patches.

Every rule sees a call through the module's own imports: `import
subprocess as sp` and `from subprocess import run as sh` resolve to
`subprocess.run`. A name the module rebinds itself (a parameter, a local
`def`, an assignment) is unknown, so a local `run(cmd, shell=True)` or an
AST visitor's own `eval(node)` never fires. A `shell=` / `verify=` /
`usedforsecurity=` value that is not a literal is ambiguous and stays quiet.

- `SHELL-001`: `subprocess.run` / `call` / `check_call` /
  `check_output` / `Popen` with `shell=True`; `os.system`; `os.popen`.
- `DESERIAL-001`: `pickle.load(s)`, `marshal.load(s)`, `dill.load(s)`,
  `cPickle.load(s)`, `yaml.load` without `SafeLoader` / `CSafeLoader` /
  `BaseLoader` as the `Loader=` keyword or the second positional argument,
  `yaml.unsafe_load`.
- `EVAL-001`: `eval` / `exec` / `compile` (bare or as `builtins.eval`)
  where the first argument is not a string literal. (Literal-arg
  `compile(...)` flows are accepted.)
- `CRYPTO-001`: `hashlib.md5` / `hashlib.sha1` / `hashlib.new("md5"/"sha1",
  ...)` used for security (`usedforsecurity=False` is honoured on all
  three), `ssl.PROTOCOL_SSLv2` / `SSLv3` / `TLSv1` / `TLSv1_1` referenced
  anywhere but as a comparison operand (`if proto == ssl.PROTOCOL_TLSv1:
  reject()` is a guard, not a use).
- `TLS-001`: `requests` / `httpx` call with `verify=False`, `aiohttp` call
  with `ssl=False` / `verify_ssl=False`, `ssl._create_unverified_context`,
  `ssl.CERT_NONE` referenced anywhere but as a comparison operand.
- `DEBUG-001`: `Flask(...)` / `FastAPI(...)` constructor with
  `debug=True`, `*.run(debug=True)` / `*.run_server(debug=True)`,
  module-level `DEBUG = True` in `*settings.py` / `*config.py`.

Each rule has a positive + negative unit test under
`tests/unit/test_security_calls.py` locking the AST shape.

---

## Security patterns: `AUTHN-001` / `SQL-001` / `SECRETPY-001`

- `AUTHN-001`: default-on. `@router.post` / `put` / `patch` / `delete`
  handlers must have an auth dependency: `Depends(get_current_user)` /
  `Depends(require_*)` (or `Security(...)`) in a parameter annotation, a
  parameter default (`user: User = Depends(get_current_user)`), or the
  decorator's `dependencies=[...]` list. FastAPI-shaped;
  the rule checks for **authentication presence only**, not
  authorisation. Only endpoint files under `api/` are scanned; when the
  package sits under a nested directory (a src layout), set the top-level
  `[tool.lanorme] source_root` (e.g. `"src/myapp"`) so the layer is found
  relative to it, or no endpoint is inspected at all. Exempt
  endpoints: `login`, `logout`, `refresh`, `token`.
- `SQL-001`: default-on. AST-based: only flags SQL string literals that
  reach a database execution sink (`.execute` / `.executemany` /
  `.executescript` on a DB-shaped receiver, or `read_sql` /
  `read_sql_query`). Unwraps `text(...)` constructors, resolves module-
  level and function-local string constants, and treats `%`-formatted /
  `.format`-built SQL, or a `+` join with a non-literal side, as
  interpolated (always flagged); a `+` join of literals and literal
  constants only is static. Static SQL passed alongside a `params=` /
  `parameters=` kwarg (or a second positional on `.execute`) with
  placeholder marks (`:name`, `%s`, `?`) is treated as safely
  parameterised and not flagged. Excludes `alembic/` and `test_*` files.
  Measured against `evals/corpora/security_raw_sql/` (125 labels):
  **P = 1.000 / R = 1.000 / F1 = 1.000**. Known limitations not in the corpus: SQL
  built across multiple statements with helper functions; lazy-loaded
  query templates; non-Python query files.
- `SECRETPY-001`: default-on. Lives in the `secrets` check. AST-based:
  flags credential-named assignments
  (variable, dict key, or call kwarg) whose value is a string or bytes
  literal that looks like a real secret, plus shape-only matches (PEM
  private-key blocks, JWT-shaped tokens, Bearer headers, DB / cache URLs
  with embedded `user:pass@host` credentials, and vendor-prefixed
  credentials: AWS `AKIA` / `ASIA`, GitHub `ghp_` / `gho_` /
  `github_pat_`, Slack `xox*`, Stripe `sk_live_` / `sk_test_`, Django
  `django-insecure-`), wherever the literal sits, including as the
  fallback of an `os.environ.get(...)` call. Names whose first segment is
  `help_` / `hint_` / `msg_` / etc. are documentation; names whose last
  segment is structural (`pattern`, `endpoint`, `header`, `name`, `len`,
  `env`, `var`, `id`, `file`, `algorithm`, `backend`, ...) point at a
  secret rather than hold one, unless the whole name is a credential
  phrase (`aws_access_key_id`). Placeholder markers (`<your-...>`, `REPLACE_ME`,
  `example`, ...) skip a value unless it is high-entropy enough (32+
  chars, mixed case, digits) to defeat the marker (AWS docs-style
  example secret keys). Excludes `conftest.py`, `seed_dev.py`, and
  files starting with `test_`. Measured against
  `evals/corpora/security_hardcoded_secrets/` (169 labels):
  **P = 1.000 / R = 1.000 / F1 = 1.000**. **Scope warning**:
  Python-source only; `.env`, `*.yaml`, `*.ipynb`, `*.tf`, `Dockerfile`,
  GitHub Actions workflows are out of scope until a separate
  non-Python rule lands.

---

## Skills: `SKILL-001..006`

On by default. Validates files named `SKILL.md` against the [Agent Skills
specification](https://agentskills.io/specification). It only fires where a
`SKILL.md` exists, so it is silent on projects without skills. The frontmatter
parser is stdlib only and never turns its own uncertainty into a failure: if a
required value cannot be read cleanly it warns `SKILL-006` rather than reporting
the value as missing.

Build failing:

- `SKILL-001`: `name` is required; 1 to 64 characters; lowercase `a-z`, digits
  and hyphens only; no leading, trailing or consecutive hyphen; and it must match
  the parent directory name.
- `SKILL-002`: `description` is required, non-empty, and at most 1024 characters.
- `SKILL-003`: optional fields are well formed: `compatibility` at most 500
  characters, `metadata` a map of string keys to string values, and
  `allowed-tools` a single string.

Advisory warnings:

- `SKILL-004`: the `SKILL.md` body stays under 500 lines (progressive disclosure).
- `SKILL-005`: relative Markdown links resolve to a file that exists. Links in
  fenced code blocks, external URLs, and `#anchors` are ignored.
- `SKILL-006`: the frontmatter is present but could not be parsed with confidence.

Config:
```toml
[tool.lanorme.skills]
enabled     = true   # default
check_links = true   # default; set false to skip SKILL-005
```

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

- `JUNK-001`: files matching scratch / temp / OS / build name globs such
  as `screenshot*`, `scratch*`, `untitled*`, `*~`, `*.bak`, `*.orig`,
  `*.rej`, `*.swp`, `*.swo`, `*.tmp`, `tmp.*`, `temp.*`, `.DS_Store`,
  `Thumbs.db`, `desktop.ini`, `nohup.out`, `core.[0-9]*` (a core dump, not a
  `core.py` module), `*.pyc`, `*.pyo`, `.coverage`, `.coverage.*`,
  `coverage.xml`.
- `JUNK-002`: image / binary extensions outside an asset directory.
  Default extensions: `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.webp`.
  Default asset directories: `assets/`, `static/`, `images/`, `img/`,
  `media/`, `public/`, `docs/`, `.github/`, `fixtures/`, `resources/`.

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

## Strong types: `TYPE-001..004`

Default-on. Skips files under `tests/` and `migrations/`. `TYPE-001..003` are
build-failing; `TYPE-004` is an advisory warning.

- `TYPE-001`: `dict[str, Any]` (and other weakly-typed dict containers)
  in function signatures or return annotations. Pushes toward DTOs,
  TypedDicts, and value objects.
- `TYPE-002`: bare `dict` / `list` / `tuple` / `set` without type
  parameters.
- `TYPE-003`: `**kwargs` must be annotated with a concrete type or
  `Unpack[TypedDict]`; bare `**kwargs: Any` is rejected.

For all three, a wrapper changes nothing: `dict[str, Any] | None`,
`Optional[dict]` and `list[dict[str, Any]]` carry the same weak type, and a
qualified `typing.Any` reads as `Any`.
- `TYPE-004` (advisory warning): a function with at least one annotated
  parameter that returns a real value in its own scope should also declare a
  return type. This is the high-signal completeness subset of ruff's `ANN`,
  not blanket presence enforcement: it fires only when the parameters are
  already typed and a value escapes, so a fully untyped function or a procedure
  that returns nothing is left alone. Generators (own-scope `yield`) are exempt;
  returns inside a nested `def` or `lambda` do not count.

---

## Suppressions: `SUPPRESS-001` / `SUPPRESS-002`

Default-off. **Opinionated.** Lives in its own `suppressions` check.

Every other rule can be switched off on a line with `# noqa` or
`# lanorme: ignore[...]`. That is deliberate, and it is also why adding
rules raises a project's ceiling without moving its floor: a rule one
comment away from off is a suggestion, not a standard. These two rules do
not close the hatches, they price them.

- `SUPPRESS-001`: the project's total inline suppressions against
  `max_total` (default 0). One finding for the project, reporting the
  count and the most-suppressed files.
- `SUPPRESS-002`: a directive that names no rule. A bare `# noqa` or an
  `ALL` code list silences every current rule on the line *and every
  future one*, so a line suppressed once quietly opts out of everything
  added since. Flagged regardless of budget.

**Neither code can be silenced inline.** `lanorme.filters` refuses
`# noqa` and `# lanorme: ignore` for the `SUPPRESS` category, because a
budget an offender can waive on the offending line is not a budget. They
remain switchable in config, and that is the point: an escape belongs in a
reviewed file, not scattered invisibly across source lines. This is the
only asymmetry of its kind in LaNorme.

Use it as a ratchet. Set `max_total` to today's count, then lower it as
debt is paid; CI fails on the next suppression added rather than on the
backlog:

```console
lanorme check . --check=suppressions
```

The check must be enabled first. With `enabled = true` and the default
`max_total = 0`, that first run fails, and its `SUPPRESS-001` message carries
today's count; set `max_total` to that number.

Comments are read through `tokenize` and matched from the start of the
comment, so a directive named in prose (a sentence such as "lines up with
`--exclude` and `# noqa` handling") or quoted in a string is documentation,
not an escape, and does not count against the budget. Nor does a `noqa` whose
codes all belong to another tool (`# noqa: E501`, `# noqa: S603`): it silences
no LaNorme rule. A LaNorme code or category anywhere in the list
(`# noqa: E501,DRY-001`) counts once.

Config:
```toml
[tool.lanorme.suppressions]
enabled       = true
max_total     = 0      # the ratchet: set to today's count, then lower it
allow_blanket = false  # set true to permit bare '# noqa'
```

This rule is a count, not a heuristic, so it carries no scored corpus.
Its correctness is pinned by `tests/unit/test_suppressions.py`, including
a regression that the `SUPPRESS` codes survive a `# noqa` naming them.

---

## Test coverage: `TESTFILE-001`

Default-on warning. For each Python file under one of the hardwired
production directories, verify that a matching `test_*.py` partner (by name
or by import reference) exists under one of the configured test roots
(`tests/integration/` by default). Note this is **file presence**, not
coverage; it cannot tell you whether the test actually exercises the module.

Findings are reported on the same path base as every other rule (relative to
`src_root`), so a `[per-file-ignores]` glob written against the path another
rule reports for a file suppresses this one too, and a baseline records it
the same way.

Config:
```toml
[tool.lanorme.test_coverage]
test_roots = ["tests/integration", "tests/unit"]
```

The production directories are looked up under the top-level
`[tool.lanorme] source_root` when one is set (`source_root = "src/myapp"`),
else under the project root and then one level down (a `src/` layout).
`test_roots` lists the directories (relative to the backend root, the parent
of that source directory) scanned for partner test files; it defaults to
`["tests/integration"]`. The scanned production directories
(`api/v1/endpoints`, `application/services`, `application/commands`,
`application/queries`, `infrastructure/repositories`,
`infrastructure/signing`, `infrastructure/secrets`) and the exempt modules
(`dependencies`, `main`, `logging`, `session`) are hardwired.

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
