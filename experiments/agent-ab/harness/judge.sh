#!/usr/bin/env bash
# Run the blind judge on one anonymised pair built by blind.py.
#
#   judge.sh BLIND_DIR PAIR OUT_DIR      e.g. judge.sh /tmp/blind stage3-run1 results/judge
#
# The judge is a fresh headless session in the pair directory, outside any
# repository, with read-only tools. It sees TASK.md (the stage prompts up to
# this stage, concatenated) and A/ and B/, never the key.
set -euo pipefail

blind=$1 pair=$2 out=$3
here=$(cd "$(dirname "$0")/.." && pwd)
model=${AGENT_AB_MODEL:-claude-opus-5-5}
stage=${pair#stage}
stage=${stage%%-*}

dir="$blind/$pair"
: > "$dir/TASK.md"
for k in $(seq 1 "$stage"); do
    { echo "# Stage $k"; echo; cat "$here/prompts/stage$k.md"; echo; } >> "$dir/TASK.md"
done

mkdir -p "$out"
cd "$dir"
timeout 3600 claude -p "$(cat "$here/prompts/judge.md")" \
    --model "$model" \
    --allowedTools "Read" "Glob" "Grep" \
    --output-format json \
    > "$out/$pair.json" 2> "$out/$pair.stderr.log"
