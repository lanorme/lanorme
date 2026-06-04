---
name: docs-audit
description: Audit the LaNorme docs for accuracy, currency, no-archeology, clarity, and coverage, then fix what it finds. Use before a release or after changing the CLI, the rules, or any Markdown doc, or when asked whether the docs are solid, current, or up to date. It runs the docs-audit workflow, which checks every documented flag, rule code, config key, and output example against the real tool, flags changelog-style narration outside CHANGELOG and any bluff, and lists undocumented surface.
license: MIT
metadata:
  project: lanorme
---

# Audit the docs

LaNorme's own `prose` check (PROSE-001/002/003) already gates em-dashes, British
spelling, and emoji on Markdown. It cannot tell whether a documented flag still
exists or an example still reproduces. This skill fills that gap: it verifies the
docs against the real tool and flags prose that is stale, narrated as history, or
puffed up.

## Run the audit

Run the `docs-audit` workflow from the repo root. Pass the absolute repo path so
the agents work in the right tree:

```
Workflow({ name: "docs-audit", args: { repo: "<absolute path to the lanorme repo>" } })
```

If the name does not resolve (for example when the working directory is a
different repository), invoke it by path instead:

```
Workflow({ scriptPath: "<repo>/.claude/workflows/docs-audit.js", args: { repo: "<repo>" } })
```

Optional `args`:

- `docs`: an explicit list of Markdown files to audit, instead of letting the
  workflow discover them.
- `cli`: how to invoke the tool if not the dev default (`PYTHONPATH=src python3 -m lanorme.cli`).

The workflow runs three phases: discover the user-facing docs and snapshot the
real CLI surface, audit each doc on two lenses (accuracy and prose) then
adversarially verify each finding, and report any user-facing surface that is
undocumented. It returns confirmed findings per doc plus a list of coverage gaps.

## Act on the result

1. Read the returned `audited` findings and `coverage_gaps`.
2. Fix accuracy issues first: a documented flag, rule code, config key, default,
   or output example that no longer matches the tool. These are the ones that
   mislead a reader.
3. Remove any changelog-style narration ("previously", "now", "renamed") from
   docs other than CHANGELOG. State the current behaviour directly. The
   CHANGELOG is the one place history belongs.
4. Cut bluff and redundancy. Keep the prose direct and grounded.
5. Close real coverage gaps by documenting the missing flag, format, or key in
   the doc the workflow suggests.

## Verify

After applying fixes, confirm the docs still pass LaNorme's own style gate and
that the changes hold up:

```
lanorme check README.md CONTRIBUTING.md CLAUDE.md docs/RULES.md
```

Re-run the workflow if you made large changes, and confirm every doc comes back
with verdict `solid` and no confirmed blocker or major remains.
