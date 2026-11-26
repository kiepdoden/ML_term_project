#!/bin/bash
# After recording: pull replica logs, join, oracle table, train + check 1 (R2)
# on the host (its own SF10 copy in ~/mcp_steer/hostdata, so the MEC VMs stay
# idle), and with --install put B2 means + oracle table on every replica.
# Usage: harness/after_record.sh <chains.jsonl> <out_dir> [--install]
set -eu
export INCUS_PROJECT=default   # the MEC VMs live in the default project, whatever the shell's current one
CHAINS=$1; OUT=$2; INSTALL=${3:-}
cd ~/mcp_steer/repo
mkdir -p "$OUT/logs" "$OUT/check1"
for n in 1 2 3; do for s in sql kv; do
  incus file pull mec$n/var/log/mcp/${s}_calls.jsonl "$OUT/logs/mec${n}_${s}.jsonl"
done; done
python3 agent/join_runs.py "$CHAINS" "$OUT/chains_joined.jsonl" "$OUT"/logs/*.jsonl
python3 agent/oracle_table.py "$OUT/chains_joined.jsonl" "$OUT/oracle_sql.json"
docker run --rm --cpuset-cpus 0,1,8,9 -v ~/mcp_steer/hostdata:/data10:ro -v "$PWD":/app \
    -e DB=/data10/tpch.duckdb mcp-steer:0.1 \
    python predict/train.py "$OUT/chains_joined.jsonl.calls.csv" "$OUT/check1" \
    | tee "$OUT/check1/train_stdout.txt"
docker run --rm --cpuset-cpus 0,1,8,9 -v "$PWD":/app mcp-steer:0.1 \
    python predict/analyze.py "$OUT/check1" | tee "$OUT/check1/analyze_stdout.txt"
if [ "$INSTALL" = "--install" ]; then
  for n in 1 2 3; do
    incus file push "$OUT/check1/b2_sql.json" mec$n/data10/b2_sql.json
    incus file push "$OUT/check1/b2_kv.json" mec$n/data/b2_kv.json
    incus file push "$OUT/oracle_sql.json" mec$n/data10/oracle_sql.json
    incus file push "$OUT/check1/runtime_model.pkl" mec$n/data10/runtime_model.pkl
    incus exec mec$n -- systemctl restart mcp-sql mcp-kv
  done
  echo installed on replicas
fi
echo AFTER_RECORD_DONE
