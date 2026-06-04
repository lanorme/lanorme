#!/usr/bin/env bash
# Keep the generated agent artifacts in sync with their canonical sources.
#
#   canonical source              ->  generated copy
#   ---------------------------------------------------------------
#   AGENTS.md                     ->  CLAUDE.md
#   .claude/skills/<name>/SKILL.md ->  .agents/skills/<name>.md
#
# The copies are plain files (no symlinks), so they work on any filesystem and
# any client. Run this after editing AGENTS.md or any skill.
#
# Usage:
#   scripts/sync-agents.sh           # regenerate the copies
#   scripts/sync-agents.sh --check   # verify they are in sync (no writes); exits
#                                     # non-zero if anything is stale (CI / gate)
#
# Depends only on cp/cmp/find. No project or runtime dependencies.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

mode="${1:-sync}"

pairs=("AGENTS.md:CLAUDE.md")
for skill in .claude/skills/*/SKILL.md; do
  [[ -e "$skill" ]] || continue
  name="$(basename "$(dirname "$skill")")"
  pairs+=("$skill:.agents/skills/$name.md")
done

case "$mode" in
  --check)
    stale=0
    for pair in "${pairs[@]}"; do
      src="${pair%%:*}"; dest="${pair##*:}"
      if [[ ! -f "$dest" ]] || ! cmp -s "$src" "$dest"; then
        echo "out of sync: $dest" >&2
        stale=1
      fi
    done
    if [[ "$stale" != 0 ]]; then
      echo "Run scripts/sync-agents.sh to regenerate the agent artifacts." >&2
      exit 1
    fi
    echo "agent artifacts in sync"
    ;;
  sync)
    find .agents/skills -maxdepth 1 -name '*.md' -delete 2>/dev/null || true
    mkdir -p .agents/skills
    for pair in "${pairs[@]}"; do
      src="${pair%%:*}"; dest="${pair##*:}"
      mkdir -p "$(dirname "$dest")"
      cp "$src" "$dest"
    done
    echo "synced ${#pairs[@]} agent artifacts"
    ;;
  *)
    echo "usage: scripts/sync-agents.sh [--check]" >&2
    exit 2
    ;;
esac
