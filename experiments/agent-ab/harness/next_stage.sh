#!/usr/bin/env bash
# Wait for a run's previous stage to finish, then run STAGE.
#
#   next_stage.sh ARM N STAGE      e.g. next_stage.sh control 1 2
#
# A stage is finished once run_stage.sh has written its snapshot and exited.
set -euo pipefail

arm=$1 n=$2 stage=$3
here=$(cd "$(dirname "$0")/.." && pwd)
prev=$((stage - 1))
out="$here/runs/$arm-$n"

until [[ -f "$out/stage$prev.timing.json" && -d "$out/stage$prev" ]] \
    && ! pgrep -f "run_stage.sh $arm $n $prev\$" > /dev/null; do
    sleep 30
done
"$here/harness/run_stage.sh" "$arm" "$n" "$stage"
echo "done: $arm-$n stage $stage"
