#!/usr/bin/env python3
"""Lengthen the warm-up of finished runs after the fact: calls sent in the
first <warmup> s are marked unmeasured, so runs made with a shorter --warmup
compare with runs made with a longer one. The run start is taken as the
first measured call minus the warm-up the run was made with (meta.json).
The original client.jsonl is kept as client.jsonl.orig.
Usage: rewarm.py <warmup_s> <run dir>..."""
import json
import os
import sys

new = float(sys.argv[1])
for d in sys.argv[2:]:
    p = os.path.join(d, "client.jsonl")
    old = json.load(open(p + ".meta.json"))["warmup"]
    if old >= new:
        print(f"{d}: warmup already {old:g} s")
        continue
    src = p + ".orig" if os.path.exists(p + ".orig") else p
    rows = [json.loads(x) for x in open(src) if x.strip()]
    t_meas = min(r["t_send"] for r in rows if r.get("measured"))
    cut = t_meas + (new - old)
    if src == p:
        os.replace(p, p + ".orig")
    n = 0
    with open(p, "w") as f:
        for r in rows:
            if r.get("measured") and r["t_send"] < cut:
                r["measured"], n = False, n + 1
            f.write(json.dumps(r) + "\n")
    print(f"{d}: warmup {old:g} -> {new:g} s, {n} calls no longer measured")
