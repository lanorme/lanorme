#!/usr/bin/env bash
# Run one stage of one agent run, then snapshot the codebase into runs/.
#
#   run_stage.sh ARM N STAGE      e.g. run_stage.sh lanorme 2 1
#
# ARM is control or lanorme. The live codebase sits in $AGENT_AB_WORK/<ARM>-<N>,
# outside any repository, so the agent sees only its own CLAUDE.md. The stage
# runs as a fresh headless Claude Code session; its transcript and the snapshot
# land in experiments/agent-ab/runs/<ARM>-<N>/.
set -euo pipefail

arm=$1 n=$2 stage=$3
here=$(cd "$(dirname "$0")/.." && pwd)
work_root=${AGENT_AB_WORK:?set AGENT_AB_WORK to a directory outside any repository}
model=${AGENT_AB_MODEL:-claude-opus-5-5}
work="$work_root/$arm-$n"
out="$here/runs/$arm-$n"

if [[ $stage == 1 ]]; then
    rm -rf "$work"
    mkdir -p "$work"
    cat "$here/arms/baseline.md" > "$work/CLAUDE.md"
    if [[ $arm == lanorme ]]; then
        cat "$here/arms/lanorme.md" >> "$work/CLAUDE.md"
    fi
    printf '%s\n' .venv/ __pycache__/ .pytest_cache/ .ruff_cache/ .mypy_cache/ .coverage > "$work/.gitignore"
    git -C "$work" init -q
    git -C "$work" add -A
    git -C "$work" -c user.name=agent-ab -c user.email=agent-ab@localhost commit -qm "stage 0"
fi

mkdir -p "$out"
start=$(date +%s)
(
    cd "$work"
    IS_SANDBOX=1 timeout 5400 claude -p "$(cat "$here/prompts/stage$stage.md")" \
        --model "$model" \
        --dangerously-skip-permissions \
        --output-format stream-json --verbose \
        > "$out/stage$stage.transcript.jsonl" 2> "$out/stage$stage.stderr.log"
) || echo "stage exited non-zero: $?" >> "$out/stage$stage.stderr.log"
end=$(date +%s)
echo "{\"arm\": \"$arm\", \"run\": $n, \"stage\": $stage, \"wall_seconds\": $((end - start))}" \
    > "$out/stage$stage.timing.json"

git -C "$work" add -A
git -C "$work" -c user.name=agent-ab -c user.email=agent-ab@localhost commit -qm "stage $stage" || true

rm -rf "$out/stage$stage"
mkdir -p "$out/stage$stage"
git -C "$work" archive HEAD | tar -x -C "$out/stage$stage"
