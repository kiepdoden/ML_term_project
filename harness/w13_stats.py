#!/usr/bin/env python3
"""Stats for the W13 check: per-run metrics, paired gains over B1 (same seed,
same calls offered), bootstrap CIs over seeds, and the p95 floor (latency
minus queue wait, i.e. what p95 would be if no call ever waited).
Usage: w13_stats.py <tag> <out.json>"""
import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
from summarize import load, pct  # noqa: E402

tag, out = sys.argv[1], sys.argv[2]
SHORT = 0.05
runs = {}
for d in sorted(glob.glob(f"results/runs/{tag}_*_s*")):
    if not os.path.isdir(d) or not os.path.exists(os.path.join(d, "client.jsonl")):
        continue
    pol, seed = os.path.basename(d)[len(tag) + 1:].rsplit("_s", 1)
    cs = [c for c in load(d) if c.get("err") is None]
    lat = [c["latency_s"] for c in cs]
    short = [c["latency_s"] for c in cs if (c["iso_run_s"] or 0) < SHORT]
    long_ = [c["latency_s"] for c in cs if (c["iso_run_s"] or 0) >= SHORT]
    floor = [c["latency_s"] - c["wait_s"] for c in cs if c.get("wait_s") is not None]
    sites = {}
    for c in cs:
        sites[c["site"]] = sites.get(c["site"], 0) + 1
    runs.setdefault(pol, {})[int(seed)] = {
        "n": len(cs), "p50": pct(lat, 50), "p95": pct(lat, 95), "p99": pct(lat, 99),
        "short_p95": pct(short, 95), "long_p95": pct(long_, 95),
        "floor_p95": pct(floor, 95), "short_share": len(short) / max(1, len(cs)),
        "sites": {k or "?": v / len(cs) for k, v in sites.items()},
        "fail": sum(1 for c in load(d) if c.get("err") is not None),
    }


def boot_mean(xs, n=2000):
    rng = random.Random(0)
    ms = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    return sum(xs) / len(xs), ms[int(0.025 * n)], ms[int(0.975 * n)]


res = {"runs": runs, "summary": {}, "paired_vs_b1": {}}
for pol, rs in runs.items():
    res["summary"][pol] = {k: sum(r[k] for r in rs.values()) / len(rs)
                           for k in ("n", "p50", "p95", "p99", "short_p95", "long_p95",
                                     "floor_p95", "short_share")}
    res["summary"][pol]["seeds"] = len(rs)
    res["summary"][pol]["fail"] = sum(r["fail"] for r in rs.values())
    res["summary"][pol]["sites"] = {s: sum(r["sites"].get(s, 0) for r in rs.values()) / len(rs)
                                    for s in ("mec1", "mec2", "mec3")}
b1 = runs.get("b1", {})
for pol, rs in runs.items():
    if pol == "b1":
        continue
    common = sorted(set(rs) & set(b1))
    if len(common) < 2:
        continue
    out_p = {}
    for m in ("p95", "short_p95", "long_p95", "p50"):
        gains = [1 - rs[s][m] / b1[s][m] for s in common]
        mean, lo, hi = boot_mean(gains)
        out_p[m] = {"mean": mean, "ci": [lo, hi], "per_seed": gains,
                    "wins": sum(g > 0 for g in gains)}
    out_p["seeds"] = common
    res["paired_vs_b1"][pol] = out_p
json.dump(res, open(out, "w"), indent=1)
for pol, s in res["summary"].items():
    print(pol, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items() if k != "sites"})
for pol, p in res["paired_vs_b1"].items():
    print(pol, "vs b1:", {m: f"{100 * v['mean']:.1f}% [{100 * v['ci'][0]:.1f}, {100 * v['ci'][1]:.1f}] wins {v['wins']}/{len(p['seeds'])}"
                          for m, v in p.items() if m != "seeds"})
