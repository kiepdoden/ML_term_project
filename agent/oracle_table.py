#!/usr/bin/env python3
"""Oracle run times for the ablation: call key (tool + arguments, the same key
the replica computes) -> median run time measured while recording.
Usage: oracle_table.py <joined chains.jsonl> <oracle_sql.json>"""
import hashlib
import json
import statistics
import sys


def call_key(tool, args):
    blob = tool + "|" + json.dumps(args, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


runs = {}
for line in open(sys.argv[1]):
    for s in json.loads(line)["steps"]:
        if s.get("run_s") is not None and s["tool"] != "kv_get":
            runs.setdefault(call_key(s["tool"], s["args"]), []).append(s["run_s"])
table = {k: statistics.median(v) for k, v in runs.items()}
json.dump(table, open(sys.argv[2], "w"))
print(f"{len(table)} distinct SQL-server calls")
