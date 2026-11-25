#!/bin/bash
# One run of one policy: controller + replay, then pull the replica logs.
# Usage: harness/run_policy.sh <policy> <run_dir> <chains.jsonl> [replay args...]
set -eu
POLICY=$1; DIR=$2; CHAINS=$3; shift 3
cd ~/mcp_steer/repo
mkdir -p "$DIR"
DUR=$(python3 - "$@" <<'EOF'
import sys
a = sys.argv[1:]
print(a[a.index("--duration") + 1] if "--duration" in a else "300")
EOF
)
sudo python3 core/controller.py --policy "$POLICY" --duration $(python3 -c "print(float('$DUR') + 20)") \
    --log "$DIR/controller.jsonl" &
CPID=$!
sleep 2
docker run --rm --cpuset-cpus 0,1,8,9 --network container:ueransim -v "$PWD":/app mcp-steer:0.1 \
    python harness/replay.py --chains "$CHAINS" --out "$DIR/client.jsonl" "$@"
sudo kill $CPID 2>/dev/null || true
wait $CPID 2>/dev/null || true
for n in 1 2 3; do
  for s in sql kv; do
    incus file pull mec$n/var/log/mcp/${s}_calls.jsonl "$DIR/server_mec${n}_${s}.jsonl"
  done
done
echo "run done: $DIR"
