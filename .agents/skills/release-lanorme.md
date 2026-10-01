---
name: release-lanorme
description: Use when cutting, shipping, releasing, or publishing a new LaNorme version (a "0.x.y" bump, a tag, a PyPI release). Runs every release gate (unit tests, the dogfood, generated docs in sync), records an eval audit (precision/recall/F1 per scored rule plus the holdout gate, stamped with the version, commit and platform) to evals/results/, bumps the version, tags, and creates the GitHub Release that auto-publishes to PyPI and deploys the versioned docs.
license: MIT
compatibility: Requires Python 3.13+, uv, git, and gh, run from a clean main checkout (the script commits whatever the tree holds).
metadata:
  project: lanorme
---

# Release LaNorme

This page explains how to cut a release and the discipline every release runs:
the gates, a recorded eval audit, regenerated docs, and the automated
publish. Creating a GitHub Release fires `.github/workflows/release.yml` (builds
and publishes to PyPI through Trusted Publishing, OIDC, no token handled) and
`.github/workflows/docs.yml` (deploys that version's docs with mike). `uv
publish` is never run by hand.

## The release discipline

Every release records the same evidence so a green tag is auditable later:

1. **Tests and dogfood** pass (`pytest tests/unit`, `lanorme check .`).
2. **Generated docs are in sync** with the tool (`scripts/gen_docs.py --check`):
   the configuration reference, JSON schema, rule index, and llms files.
3. **An eval audit is recorded** to `evals/results/vX.Y.Z.json`: precision,
   recall and F1 per scored rule against the labelled corpora (dev split,
   sealed holdout split, and the gap between them), stamped with the LaNorme
   version, the git commit, the Python version, and the platform. The script
   records it with `--no-perf`, so its `performance` block is empty.
   Performance is opt-in: `evals/audit.py` without `--no-perf` times the pinned
   corpora; those numbers are machine-dependent and informational only.
4. **No holdout regression.** The audit runs with `--gate latest`: it fails
   when any rule's holdout precision or recall falls more than 0.02 below the
   best value any comparable committed `evals/results/v*.json` recorded (one
   that scored the same holdout files), not merely the latest, and lists the
   rules. It also fails when a holdout file the newest recorded audit digested
   was removed or changed, unless the optional `evals/holdout_revisions.json` accepts that
   exact new digest with a reason. Dev numbers are informational and never
   block.
5. **RULES.md reflects the measured F1** for every rule that has a corpus. If a
   rule's F1 moved, update its line before tagging.

`scripts/release.sh` enforces steps 1 to 4 (it refuses to tag if any gate fails
or the docs are stale); step 5 is a human check on the audit output.

## Versioning

The public surface is the rule codes used in `select` / `ignore` /
`per-file-ignores` and the config keys under `[tool.lanorme]`. The deciding
question is whether a green codebase could go red on upgrade:

- patch (`0.y.z`): no existing codebase's result changes (fixes, docs, opt-in
  checks, new config keys with safe defaults).
- minor (`0.y.0`): a green codebase can newly fail (a new default-on check, a
  default-on rule made stricter, a renamed or removed rule code, a changed
  default). Before 1.0, every breaking change is a minor.
- major (`1.0.0`): the stability commitment.

The README "Versioning" section is canonical; keep them in step.

## Steps

1. Pick the new version `X.Y.Z`.
2. Add a `## [X.Y.Z]` section to `CHANGELOG.md` describing the user-facing
   changes. Required: the release notes are taken verbatim from it.
3. Regenerate the docs and review the audit numbers:

   ```
   uv run python scripts/gen_docs.py
   uv run python evals/audit.py --version X.Y.Z --no-perf --output /tmp/preview.json --gate latest
   ```

   Read the preview; if any F1 changed, update that rule's line in
   `docs/RULES.md`. A holdout regression fails the preview and the release: fix
   the rule, never the holdout files (see `CONTRIBUTING.md`). Commit the regenerated docs and any RULES.md change. Do not
   commit the audit file yourself: `release.sh` records the committed
   `evals/results/vX.Y.Z.json` for you, against the release commit (step 4), so
   its version and commit stamp match the released tree.
4. From the repo root, run the helper:

   ```
   scripts/release.sh X.Y.Z
   ```

   It refuses unless you are on `main`, the CHANGELOG section exists, the tag
   does not exist, the docs are in sync, and the gates pass (including an
   eval-audit precheck that the corpora are complete and not stale and that no
   holdout number regressed). Then it bumps the version in `pyproject.toml` and
   `src/lanorme/__init__.py`, builds, runs `twine check`, commits the release
   with `git add -A` (the bump plus the `uv.lock` uv rewrites), records the
   eval audit against that commit (a second `Record X.Y.Z eval audit` commit),
   tags `vX.Y.Z`, pushes, and creates the GitHub Release.
5. Watch the publish and docs workflows, then verify the package is live:

   ```
   gh run watch $(gh run list --workflow=release.yml --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
   uvx --refresh --from lanorme==X.Y.Z lanorme --version
   ```

   The docs workflow publishes `X.Y.Z` (and moves the `latest` alias) at
   https://lanorme.github.io/lanorme/.

## Gotchas

- `scripts/release.sh` refuses unless you are on `main`, the `## [X.Y.Z]`
  CHANGELOG section exists, the tag is new, and the gates pass. It does not
  check that the tree is clean: `git add -A` commits everything it finds, so
  start from a clean tree. It tags nothing until every gate is green.
- The version lives in **two** files (`pyproject.toml` and
  `src/lanorme/__init__.py`), and uv rewrites `uv.lock` to match, so the release
  commit changes three files. The manual fallback must bump both and stage all
  three, or the build and `lanorme --version` disagree and the next `uv run`
  dirties the tree.
- `uv publish` is never run by hand. PyPI publishing is OIDC Trusted Publishing,
  fired only by the GitHub Release.
- The eval audit's accuracy step is strict: if a scorer sees a finding that
  is not in its corpus `labels.json`, or `validate_corpora.py` finds an
  unlabelled file or comment, a label whose line hash is missing or no longer
  matches its line, a label of the wrong polarity for its directory, or a
  file off its recorded split, it errors rather than scoring a wrong number.
  That means a fixture went stale, not that the release is blocked on
  performance; fix the labels. The split is recorded per file in
  `labels.json` (the name hash only proposes one for a new file), and
  `uv run python evals/validate_corpora.py --stamp` fills a missing split or
  line hash.
- The holdout gate holds each rule to the best comparable release over the
  whole history of `evals/results/v*.json`, not the newest alone. A rule no
  comparable audit has holdout numbers for is skipped, not failed, and the
  gate prints a note when it gated nothing, so the first release after a
  corpus gains a holdout split records the baseline the next one is held to.
- Performance numbers, when you opt in, are machine-dependent. The audit stamps
  the platform and processor; do not compare them across machines.

## If something fails

- A gate (tests, dogfood, stale docs) fails: nothing is committed or tagged. Fix
  and re-run.
- The eval audit's accuracy step fails (a scorer flags an unlabelled
  finding): the corpus is out of date. Fix the labels or the fixture, re-run.
- The holdout gate fails: a change since the last release made a rule worse on
  data it was not tuned on, or a holdout file changed. Fix or revert the rule
  change; do not edit the holdout files to pass. A holdout edit that is right
  on its own merits (a label proved wrong) goes in its own reviewed change with
  an `evals/holdout_revisions.json` entry naming the new digest and the reason.
- The publish workflow fails (for example a PyPI outage): the tag and release
  already exist, so do not re-tag. Re-run with `gh run rerun <id>` or
  `gh workflow run release.yml`.

## Finish a half-done release

When the `Release X.Y.Z` and `Record X.Y.Z eval audit` commits are on `main`
but the tag or the GitHub Release is missing (for example the environment could
not push tags or create releases), the script cannot resume: preflight passes,
the gates re-run, the bump is a no-op, and the release commit fails with nothing
to commit. Finish by hand:

1. `git checkout main && git pull origin main`; confirm HEAD is the audit
   commit and `pyproject.toml` says `version = "X.Y.Z"`.
2. `git tag -a vX.Y.Z -m vX.Y.Z && git push origin vX.Y.Z` (skip if the tag
   exists).
3. `rm -rf dist && uv build`.
4. `gh release create vX.Y.Z dist/lanorme-X.Y.Z-py3-none-any.whl
   dist/lanorme-X.Y.Z.tar.gz --title vX.Y.Z --notes-file <file>`, where the
   file holds the `## [X.Y.Z]` CHANGELOG section.
5. Watch the workflows and verify with `uvx`, as in step 5 of Steps.

## Manual fallback (no script)

Edit `CHANGELOG.md`, run `uv run python scripts/gen_docs.py`, `uv run --group
dev pytest tests/unit`, `uv run lanorme check .`, and the audit precheck
(`uv run python evals/audit.py --version X.Y.Z --no-perf --output /tmp/pre.json
--gate latest`). Bump `version` in `pyproject.toml` and `__version__` in
`src/lanorme/__init__.py`, then `rm -rf dist && uv build` and `uv run --with
twine python -m twine check dist/*`. Stage `pyproject.toml`,
`src/lanorme/__init__.py` and `uv.lock`, and `git commit -m "Release X.Y.Z"`.
Record the audit against that commit: `uv run python evals/audit.py --version
X.Y.Z --no-perf` and `git commit -m "Record X.Y.Z eval audit" evals/results/`.
Finally `git tag -a vX.Y.Z -m vX.Y.Z`, `git push origin main`, `git push origin
vX.Y.Z`, and `gh release create vX.Y.Z dist/lanorme-X.Y.Z-py3-none-any.whl
dist/lanorme-X.Y.Z.tar.gz --title vX.Y.Z --notes-file <file>`, with the notes
taken from the `## [X.Y.Z]` section (the `awk` in `scripts/release.sh` extracts
it).
