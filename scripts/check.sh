#!/usr/bin/env bash
# Run every LaNorme gate: the agent-copy sync check, the generated-docs check,
# ruff, unit tests, the eval audit, the dogfood lint, and a package build.
# Run this before committing or finishing a change. No arguments.
#
#   scripts/check.sh
#
# Exits non-zero on the first failing gate. CI runs the same set; the pre-commit
# hooks run a subset (ruff, the dogfood and the tests, only when a Python file
# is staged).
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

echo "==> agent artifacts in sync"
scripts/sync-agents.sh --check

echo "==> generated docs in sync"
uv run python scripts/gen_docs.py --check

echo "==> ruff (trailing commas, formatting)"
uv run --group dev ruff check .
uv run --group dev ruff format --check .

echo "==> unit tests"
uv run --group dev pytest tests/unit -q

echo "==> eval audit (corpora complete, holdout not below the last release)"
uv run python evals/audit.py --version check --no-perf --output "$(mktemp)" --gate latest

echo "==> lint (lanorme dogfood)"
uv run lanorme check .

echo "==> build"
uv build >/dev/null

echo
echo "OK: all gates passed."
