# Configuration reference

This reference lists every top-level key in the `[tool.lanorme]` table, generated
from the tool so it cannot drift. Per-check settings (`[tool.lanorme.<check>]`) are
documented with each rule in the [rule reference](../RULES.md). A machine-readable
[`lanorme.schema.json`](https://lanorme.github.io/lanorme/lanorme.schema.json) validates this table in editors.

Configuration lives in `[tool.lanorme]` in `pyproject.toml`, or in a standalone
`lanorme.toml` / `.lanorme.toml`. **The table header differs between the two.** In
`pyproject.toml` every key sits under `[tool.lanorme]`, and a per-check table under
`[tool.lanorme.<check>]`. In a standalone `lanorme.toml` the prefix is dropped: keys
are top-level (`promote = ["TYPE-004"]`) and a sub-table is bare (`[per-file-ignores]`,
`[prose]`). The examples below show the `pyproject.toml` form; a `[tool.lanorme]` prefix
written inside a `lanorme.toml` is silently ignored. Keys also have command-line
equivalents (`--select`, `--ignore`); the command line wins over config.

| Key | Type | Default | Feature |
| --- | --- | --- | --- |
| [`select`](#select) | list of strings | all enabled checks | Filtering |
| [`ignore`](#ignore) | list of strings | [] (nothing ignored) | Filtering |
| [`exclude`](#exclude) | list of glob strings | [] (built-in junk dirs only) | Filtering |
| [`per-file-ignores`](#per-file-ignores) | table of glob to code list | {} (no per-file rules) | Filtering |
| [`promote`](#promote) | list of strings | [] (advisories stay warnings) | Severity |
| [`extends`](#extends) | string or list of strings | none | Profiles |
| [`baseline`](#baseline) | string (path) | none | Adoption |
| [`source_root`](#source_root) | string (path) | the scan root | Architecture |
| [`plugins`](#plugins) | list of strings | [] (built-in checks only) | Extensibility |
| [`root`](#root) | boolean | false | Per-directory config |

## `select`

Run only these rule codes or categories. A category is the part of a code before the dash: `CMT` covers every comment rule; `ALL` selects everything.

```toml
select = ["SECRETPY", "TYPE-004"]
```

## `ignore`

Skip these rule codes or categories everywhere.

```toml
ignore = ["NAMING-003"]
```

## `exclude`

File-path globs to skip entirely; matched files are never walked.

```toml
exclude = ["**/migrations/*", "generated/*"]
```

## `per-file-ignores`

Suppress specific rule codes or categories for files matching a glob.

```toml
[tool.lanorme.per-file-ignores]
"tests/*" = ["SIZE-001", "AAA"]
```

## `promote`

Advisory warnings whose codes (or `ALL`) become build-failing errors. Runs after every suppression, so an ignored or noqa'd warning is never promoted.

```toml
promote = ["TYPE-004"]   # or ["ALL"]
```

## `extends`

Adopt one or more profiles -- a bundled name (`strict`, `hexagonal`, `clean`, `layered`) or a path to a local `.toml`. Profiles merge left to right, then your own keys merge on top, so local always wins.

```toml
extends = ["strict", "hexagonal"]
```

## `baseline`

Path to a baseline file. Findings recorded by `lanorme baseline write` are suppressed, so only new findings report. See the adoption tutorial.

```toml
baseline = "lanorme-baseline.json"
```

## `source_root`

The top-level package directory when ports, adapters and layers live under a nested package; the architecture checks interpret their paths relative to it, and `AUTHN-001` finds the `api/` layer under it.

```toml
source_root = "src/myapp"
```

## `plugins`

Extra check modules to import so they self-register, beyond the built-ins and entry-point plugins.

```toml
plugins = ["my_company.lanorme_checks"]
```

## `root`

In a nested config file, stop inheriting from the config files above it, so the subtree stands alone. See [per-directory config](#per-directory-config).

```toml
root = true
```

## Per-directory config

Any directory below the scan root that carries its own config file (a
`lanorme.toml`, a `.lanorme.toml`, or a `pyproject.toml` with a `[tool.lanorme]`
table) is a region: the files beneath it are checked under that config. A nested
region inherits every setting from the regions above it and overrides only the
keys it sets, table by table and key by key, so one subtree can tighten or relax
a threshold without restating the whole config. `root = true` in a nested file
stops the inheritance, so the subtree stands alone.

```toml
# legacy/lanorme.toml: looser limits for the old tree only
[file_limits]
func_warn_lines = 80
```

Cascading governs per-check settings and `source_root`. The run-level filters
(`select`, `ignore`, `exclude`, `per-file-ignores`, `promote`) are read once at
the root and apply to the whole run. Checks that compare files across the tree
(`duplication`, `test_coverage`, `layer_deps`, `port_coverage`, `docs`, `meta`)
run once at the scan root under the root config, so a region cannot relax them
for its own subtree. `--check <name>` runs that one check at the root config too,
without cascading.

## Per-check settings

Each check is configured under its own table. An opt-in check carries an
`enabled` toggle that defaults to `false`; a default-on check has no toggle, and
its table sets only the keys its rule reference lists. The settings a check
accepts are listed in its [rule reference](../RULES.md) section. For example:

```toml
[tool.lanorme.prose]
enabled = true

[tool.lanorme.layer_deps]
composition_root = ["api/dependencies.py"]
```
