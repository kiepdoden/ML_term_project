#!/bin/bash
# 4 Dec sweep: load x skew (background share of MEC-1), B0/B1/B2/method, 3 seeds.
# Seed outer, so each finished seed is a full grid. Usage: harness/sweep.sh ["seeds"]
# (default "1 2 3"); finished runs are skipped, so it also resumes a broken sweep.
cd ~/mcp_steer/repo
export INCUS_PROJECT=default
SEEDS=${1:-1 2 3}
C=results/rec_sf10/chains_joined.jsonl
BASE="--tasks results/rec_sf10/check1/test_tasks.txt --clients 90 --think-scale 0.2 --capacity 4.3 --bg-workers 1.43 --duration 240 --warmup 60"
for seed in $SEEDS; do
  for L in 0.1 0.2 0.3 0.5 0.7 0.9; do
    for K in 0 0.3 0.6; do
      T=sw_l${L/0./}_k${K/0./}
      harness/run_matrix.sh $T "b0 b1 b2 method" "$seed" $C $BASE --load $L --bg-load $K
    done
  done
  echo "$(date +%T) SEED_DONE $seed"
done
echo SWEEP_DONE
