#!/usr/bin/env python3
"""Stats for the load x skew sweep (tags sw_l<load>_k<skew>, see sweep.sh).

Per cell and policy: mean over seeds of p50 / p95 / short-call p95 / long-call
p95 and the share served at each site; paired gains (same seed) of each
policy over B0 and over B1. A cell's crossover flag says whether steering
(the method) beats staying at the nearest site (B0).
Usage: sweep_stats.py <out.json> [prefix=sw]"""
import glob
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from summarize import load, pct  # noqa: E402

out = sys.argv[1]
prefix = sys.argv[2] if len(sys.argv) > 2 else "sw"
SHORT = 0.05
METRICS = ("p50", "p95", "p99", "short_p95", "long_p95")


def run_stats(d):
    cs = [c for c in load(d) if c.get("err") is None]
    if not cs:
        return None
    lat = [c["latency_s"] for c in cs]
    short = [c["latency_s"] for c in cs if (c["iso_run_s"] or 0) < SHORT]
    long_ = [c["latency_s"] for c in cs if (c["iso_run_s"] or 0) >= SHORT]
    sites = {}
    for c in cs:
        sites[c["site"]] = sites.get(c["site"], 0) + 1
    return {"n": len(cs), "p50": pct(lat, 50), "p95": pct(lat, 95), "p99": pct(lat, 99),
            "short_p95": pct(short, 95), "long_p95": pct(long_, 95),
            "sites": {k or "?": v / len(cs) for k, v in sites.items()},
            "fail": sum(1 for c in load(d) if c.get("err") is not None)}


def boot_mean(xs, n=2000):
    rng = random.Random(0)
    ms = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    return sum(xs) / len(xs), ms[int(0.025 * n)], ms[int(0.975 * n)]


cells = {}
pat = re.compile(rf"{prefix}_l(\d+)_k(\d+)_(\w+)_s(\d+)$")
for d in sorted(glob.glob(f"results/runs/{prefix}_l*_k*_*_s*")):
    m = pat.match(os.path.basename(d))
    if not m or not os.path.isdir(d) or not os.path.exists(os.path.join(d, "client.jsonl")):
        continue
    L, K, pol, seed = int(m[1]) / 10, int(m[2]) / 10, m[3], int(m[4])
    st = run_stats(d)
    if st:
        cells.setdefault((L, K), {}).setdefault(pol, {})[seed] = st

rows = []
for (L, K), pols in sorted(cells.items()):
    row = {"load": L, "skew": K, "policies": {}, "gain": {}}
    for pol, rs in pols.items():
        s = {k: sum(r[k] for r in rs.values()) / len(rs) for k in METRICS + ("n",)}
        s["seeds"] = sorted(rs)
        s["fail"] = sum(r["fail"] for r in rs.values())
        s["sites"] = {x: sum(r["sites"].get(x, 0) for r in rs.values()) / len(rs)
                      for x in ("mec1", "mec2", "mec3")}
        row["policies"][pol] = s
    for ref in ("b0", "b1"):
        for pol, rs in pols.items():
            if pol == ref or ref not in pols:
                continue
            common = sorted(set(rs) & set(pols[ref]))
            if not common:
                continue
            g = {}
            for m in ("p95", "short_p95", "p50"):
                xs = [1 - rs[s][m] / pols[ref][s][m] for s in common]
                mean, lo, hi = boot_mean(xs) if len(xs) > 1 else (xs[0], xs[0], xs[0])
                g[m] = {"mean": mean, "ci": [lo, hi], "per_seed": xs}
            row["gain"][f"{pol}_vs_{ref}"] = g
    p = row["policies"]
    if "method" in p and "b0" in p:
        row["steer_pays"] = p["method"]["p95"] < p["b0"]["p95"]
    rows.append(row)

json.dump({"cells": rows}, open(out, "w"), indent=1)
print(f"{'load':>4} {'skew':>4} | " + " | ".join(f"{p:>14}" for p in ("b0", "b1", "b2", "method"))
      + " | method vs b1 | method vs b0")
for r in rows:
    p = r["policies"]
    cells_txt = " | ".join(
        f"{p[x]['p95']:6.2f}/{p[x]['short_p95']:5.2f}s" if x in p else f"{'-':>14}"
        for x in ("b0", "b1", "b2", "method"))
    g1 = r["gain"].get("method_vs_b1", {}).get("p95")
    g0 = r["gain"].get("method_vs_b0", {}).get("p95")
    print(f"{r['load']:4.1f} {r['skew']:4.1f} | {cells_txt} | "
          f"{(100 * g1['mean']):+6.1f}%      | {(100 * g0['mean']):+6.1f}%" if g1 and g0 else
          f"{r['load']:4.1f} {r['skew']:4.1f} | {cells_txt}")
