#!/usr/bin/env python3
"""Join recorded chains with the replicas' logs (call_id): every step gets the
run time and queue wait the replica measured, and whether the tool failed.
Usage: join_runs.py <chains.jsonl> <out.jsonl> <server_log.jsonl> [...]
Also writes <out>.calls.csv with one row per call (the predictor dataset)."""
import csv
import json
import sys

chains_path, out_path, *logs = sys.argv[1:]
srv = {}
for p in logs:
    for line in open(p):
        r = json.loads(line)
        if r.get("call_id"):
            srv[r["call_id"]] = r

n_join = n_miss = 0
rows = []
with open(out_path, "w") as f:
    for line in open(chains_path):
        c = json.loads(line)
        for i, s in enumerate(c["steps"]):
            r = srv.get(s["call_id"])
            if r is None:
                n_miss += 1
                s["run_s"] = None
                continue
            n_join += 1
            s["run_s"] = r["run_s"]
            s["wait_s"] = r["wait_s"]
            s["site"] = r["site"]
            s["err"] = r["err"]
            rows.append({"task": c["task"], "template": c["template"], "run": c["run"],
                         "step": i, "tool": s["tool"],
                         "args": json.dumps(s["args"], sort_keys=True),
                         "run_s": r["run_s"], "wait_s": r["wait_s"], "site": r["site"],
                         "err": r["err"] or "", "think_s": s["think_s"]})
        f.write(json.dumps(c) + "\n")
with open(out_path + ".calls.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"joined {n_join} calls, {n_miss} without a replica log entry")
