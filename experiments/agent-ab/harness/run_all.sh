#!/usr/bin/env bash
# Run every arm and run index through all three stages, runs in parallel.
#
#   run_all.sh [RUNS]             default RUNS=3
#
# Stages of one run are sequential (each builds on the last); the 2 x RUNS
# runs proceed side by side.
set -euo pipefail

runs=${1:-3}
harness=$(cd "$(dirname "$0")" && pwd)

run_one() {
    local arm=$1 n=$2
    for stage in 1 2 3; do
        "$harness/run_stage.sh" "$arm" "$n" "$stage"
        echo "done: $arm-$n stage $stage"
    done
}

for n in $(seq 1 "$runs"); do
    for arm in control lanorme; do
        run_one "$arm" "$n" &
    done
done
wait
echo "all runs finished"
