#!/bin/bash
# Run several policies x seeds on the same chains and settings.
# Usage: harness/run_matrix.sh <tag> "<policies>" "<seeds>" <chains> [replay args...]
# e.g.  harness/run_matrix.sh base "b0 b1" "1 2 3" results/rec_sf10/chains_joined.jsonl \
#         --tasks results/rec_sf10/check1/test_tasks.txt --clients 30 --load 0.7 --bg-load 0.3
set -eu
TAG=$1; POLICIES=$2; SEEDS=$3; CHAINS=$4; shift 4
cd ~/mcp_steer/repo
mkdir -p results/runs
for seed in $SEEDS; do
  for p in $POLICIES; do            # policies interleaved per seed: drift hits all alike
    D=results/runs/${TAG}_${p}_s${seed}
    [ -f "$D/client.jsonl" ] && { echo "skip $D"; continue; }
    # empty the replica logs so each run's pull holds only that run
    for n in 1 2 3; do incus exec mec$n -- sh -c 'truncate -s0 /var/log/mcp/sql_calls.jsonl /var/log/mcp/kv_calls.jsonl'; done
    harness/run_policy.sh "$p" "$D" "$CHAINS" --seed "$seed" "$@" > "$D.log" 2>&1 || echo "FAILED $D"
    echo "$(date +%T) done $D"
  done
done
