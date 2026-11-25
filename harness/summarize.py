#!/usr/bin/env python3
"""Summarize runs: join client calls with replica logs (call_id), report
p50/p95 latency overall and per slice, and where calls were served.
Usage: summarize.py <run_dir> [<run_dir> ...]"""
import glob
import json
import os
import sys


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = max(0, min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1)))))
    return xs[k]


def load(d):
    calls = [json.loads(l) for l in open(os.path.join(d, "client.jsonl"))]
    srv = {}
    for f in glob.glob(os.path.join(d, "server_*.jsonl")):
        for l in open(f):
            r = json.loads(l)
            if r.get("call_id"):
                srv[r["call_id"]] = r
    for c in calls:
        s = srv.get(c["call_id"])
        c["site"] = s["site"] if s else None
        c["wait_s"] = s["wait_s"] if s else None
        c["run_s"] = s["run_s"] if s else None
    return [c for c in calls if c.get("measured") and not c.get("bg")]


def summary(calls, short_cut=0.05):
    lat = [c["latency_s"] for c in calls if c.get("err") is None]
    short = [c["latency_s"] for c in calls if c.get("err") is None and (c["iso_run_s"] or 0) < short_cut]
    long_ = [c["latency_s"] for c in calls if c.get("err") is None and (c["iso_run_s"] or 0) >= short_cut]
    sites = {}
    for c in calls:
        sites[c["site"]] = sites.get(c["site"], 0) + 1
    waits = [c["wait_s"] for c in calls if c["wait_s"] is not None]
    return {
        "n": len(calls), "fail": sum(not c["ok"] for c in calls),
        "p50_ms": 1000 * pct(lat, 50), "p95_ms": 1000 * pct(lat, 95),
        "short_p95_ms": 1000 * pct(short, 95), "long_p95_ms": 1000 * pct(long_, 95),
        "wait_p95_ms": 1000 * pct(waits, 95),
        "sites": {k or "?": v for k, v in sorted(sites.items(), key=lambda x: str(x[0]))},
    }


if __name__ == "__main__":
  for d in sys.argv[1:]:
    s = summary(load(d))
    print(os.path.basename(d.rstrip("/")), json.dumps({k: (round(v, 1) if isinstance(v, float) else v)
                                                       for k, v in s.items()}))
