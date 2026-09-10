# LaNorme

LaNorme makes a codebase's standard executable. It is a precision-first
Python linter with zero runtime dependencies (standard library only) that
turns the rules your team has agreed on into checks that run on every commit.

Precision comes first. A false positive is the cardinal sin: a check that
cries wolf trains people to ignore it, so LaNorme would rather stay silent
than flag code that is fine. Opinionated rules report as warnings, which keep
the exit code at `0`, until you promote them to errors.

## 30-second example

Install LaNorme as a development dependency:

```bash
uv add --dev lanorme
```

Or with pip:

```bash
pip install lanorme
```

Then check your project:

```bash
lanorme check .
```

A clean run reports that every check passed and exits `0`:

```text
All 30 checks passed.
```

Exit codes are `0` when no check fails (warnings alone leave it at `0`), `1`
when a check fails (a violation, or a warning you have promoted), and `2` on a
usage or configuration error, so the command drops straight into a pre-commit
hook or CI step.

Two shortcuts help while you work:

- `lanorme rule <CODE>` prints a single rule's reference section in the
  terminal, for example `lanorme rule DRY-001`.
- Every page on this site is also published as raw Markdown at its source
  path: replace the trailing `/` of a page URL with `.md`, and use `index.md`
  for the home page and each section landing page. The "View as Markdown"
  button on a page opens the same file.

## Where to go next

- [Adopt LaNorme on an existing codebase](tutorials/adopt-on-existing-codebase.md):
  a tutorial that records a baseline so only new findings fail the build while
  the recorded debt stays quiet (see all of it any time with
  `lanorme check --no-baseline` or `lanorme baseline status`).
- [How-to guides](how-to/index.md): task-focused recipes for choosing which
  checks run and excluding paths, promoting warnings to errors, using
  configuration profiles, and writing a custom check.
- Reference: the [configuration reference](reference/configuration.md) for
  every `[tool.lanorme]` key, and [the rules](RULES.md) for each check and its
  per-check settings.
- [Precision first](explanation/precision-first.md): why LaNorme treats a
  false positive as the cardinal sin, and what that costs and buys.
