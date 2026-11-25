#!/usr/bin/env python3
"""Policy table over runs: results/runs/<tag>_<policy>_s<seed>/
Per policy: p50, p95 (with a 95% bootstrap CI that resamples task runs, so
calls of one task stay together), p95 of short and long calls, task time,
failures, and where the calls were served.
Usage: table.py <tag> [--short-cut 0.05] [--md out.md]"""
import argparse
import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
from summarize import load, pct  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("tag")
ap.add_argument("--short-cut", type=float, default=0.05)
ap.add_argument("--md", default="")
ap.add_argument("--boot", type=int, default=1000)
a = ap.parse_args()

by_pol = {}
for d in sorted(glob.glob(f"results/runs/{a.tag}_*_s*")):
    if not os.path.isdir(d) or not os.path.exists(os.path.join(d, "client.jsonl")):
        continue
    pol = os.path.basename(d)[len(a.tag) + 1:].rsplit("_s", 1)[0]
    by_pol.setdefault(pol, []).append((d, load(d)))


def p95_ci(calls):
    groups = {}
    for c in calls:
        groups.setdefault((c["_run"], c["task_run"]), []).append(c["latency_s"])
    keys = list(groups)
    rng = random.Random(0)
    vals = []
    for _ in range(a.boot):
        xs = [x for k in (rng.choice(keys) for _ in keys) for x in groups[k]]
        vals.append(pct(xs, 95))
    return pct(vals, 2.5), pct(vals, 97.5)


rows = []
for pol, runs in by_pol.items():
    calls = []
    for i, (d, cs) in enumerate(runs):
        for c in cs:
            c["_run"] = i
        calls += [c for c in cs if c.get("err") is None]
    lat = [c["latency_s"] for c in calls]
    short = [c["latency_s"] for c in calls if (c["iso_run_s"] or 0) < a.short_cut]
    long_ = [c["latency_s"] for c in calls if (c["iso_run_s"] or 0) >= a.short_cut]
    tasks = {}
    for c in calls:
        k = (c["_run"], c["task_run"])
        t0, t1 = tasks.get(k, (c["t_send"], c["t_recv"]))
        tasks[k] = (min(t0, c["t_send"]), max(t1, c["t_recv"]))
    sites = {}
    for c in calls:
        sites[c["site"]] = sites.get(c["site"], 0) + 1
    lo, hi = p95_ci(calls)
    fails = sum(1 for _, cs in runs for c in cs if c.get("err") is not None)
    rows.append({
        "policy": pol, "runs": len(runs), "calls": len(calls), "fail": fails,
        "p50_ms": 1000 * pct(lat, 50), "p95_ms": 1000 * pct(lat, 95),
        "p95_ci": f"{1000 * lo:.0f}-{1000 * hi:.0f}",
        "short_p95_ms": 1000 * pct(short, 95), "long_p95_ms": 1000 * pct(long_, 95),
        "per_run_p95_ms": [round(1000 * pct([c["latency_s"] for c in cs if c.get("err") is None], 95))
                           for _, cs in runs],
        "sites_%": {k: round(100 * v / len(calls)) for k, v in sorted(sites.items(), key=str)},
    })

for r in rows:
    print(json.dumps({k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items()}))
with open(f"results/runs/{a.tag}_table.json", "w") as f:
    json.dump(rows, f, indent=1)
if a.md:
    with open(a.md, "w") as f:
        f.write("| Policy | Runs | Calls | p50 ms | p95 ms (95% CI) | p95 short | p95 long | "
                "Served at MEC-1/2/3 (%) |\n|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            s = r["sites_%"]
            f.write(f"| {r['policy']} | {r['runs']} | {r['calls']} | {r['p50_ms']:.0f} | "
                    f"{r['p95_ms']:.0f} ({r['p95_ci']}) | {r['short_p95_ms']:.0f} | "
                    f"{r['long_p95_ms']:.0f} | {s.get('mec1', 0)}/{s.get('mec2', 0)}/"
                    f"{s.get('mec3', 0)} |\n")
