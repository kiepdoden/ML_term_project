#!/bin/bash
# Method v2 (expected local arrivals in the wait): deploy to the replicas and run
#   A. the cells where v1 lost to B1 (load 10-30%, local users 30/60% of MEC-1)
#   B. the main setting (load 70%, local users 30%), 10 seeds, tag w13, so v2 is
#      paired by seed with the W13 runs of B1 and v1
#   C. (optional) the rest of the sweep grid with local users (load 50-90%)
# With no local users (skew 0) v2 reports the same wait as v1, so those cells
# are not run again.
# Usage: harness/v2_runs.sh [--deploy] [phases, default "A B"]
set -eu
cd ~/mcp_steer/repo
export INCUS_PROJECT=default
DEPLOY=0
[ "${1:-}" = "--deploy" ] && { DEPLOY=1; shift; }
PHASES=${*:-A B}
C=results/rec_sf10/chains_joined.jsonl
COMMON="--tasks results/rec_sf10/check1/test_tasks.txt --clients 90 --think-scale 0.2 --capacity 4.3 --bg-workers 1.43"
SW="$COMMON --duration 240 --warmup 60"
HL=results/runs/host_load.jsonl

if [ $DEPLOY = 1 ]; then
  for n in 1 2 3; do
    for f in mcpsite.py sql_server.py kv_server.py; do incus file push mec/$f mec$n/opt/mcpsite/$f; done
    incus exec mec$n -- systemctl restart mcp-sql mcp-kv
  done
  sleep 20
fi
for n in 1 2 3; do              # every replica must report the v2 wait
  curl -sf --max-time 5 http://10.201.$n.10:8000/status | grep -q wait_pred2_ms \
    || { echo "mec$n does not report wait_pred2_ms"; exit 1; }
done
echo "$(date +%T) replicas report wait_pred2_ms"

# CPU seconds of every instance that is not a MEC site, to see if others used the host
others() {
  incus query "/1.0/instances?recursion=2&all-projects=true" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(json.dumps({i["name"]: round((i.get("state") or {}).get("cpu", {}).get("usage", 0) / 1e9, 1)
                  for i in d if i["name"] not in ("mec1", "mec2", "mec3")}))'
}
one() {   # one run: tag, policy, seed, args...
  local tag=$1 pol=$2 seed=$3; shift 3
  local before; before=$(others || echo "{}")
  harness/run_matrix.sh "$tag" "$pol" "$seed" $C "$@"
  echo "{\"run\": \"${tag}_${pol}_s${seed}\", \"t\": $(date +%s), \"load\": \"$(cut -d' ' -f1-3 /proc/loadavg)\", \"others_before\": $before, \"others_after\": $(others || echo "{}")}" >> $HL
}

for ph in $PHASES; do
  case $ph in
    A) for seed in 1 2 3; do for L in 0.1 0.2 0.3; do for K in 0.3 0.6; do
         one sw_l${L/0./}_k${K/0./} method2 $seed $SW --load $L --bg-load $K
       done; done; done ;;
    # W13 runs drop the first 60 s. The v2 runs in results/raw were made with the
    # 30 s default and aligned afterwards with harness/rewarm.py 60.
    B) for seed in $(seq 1 10); do
         one w13 method2 $seed $COMMON --warmup 60 --load 0.7 --bg-load 0.3
       done ;;
    C) for seed in 1 2 3; do for L in 0.5 0.7 0.9; do for K in 0.3 0.6; do
         one sw_l${L/0./}_k${K/0./} method2 $seed $SW --load $L --bg-load $K
       done; done; done ;;
  esac
  echo "$(date +%T) PHASE_DONE $ph"
done
echo V2_DONE
