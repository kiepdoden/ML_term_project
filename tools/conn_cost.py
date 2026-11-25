#!/usr/bin/env python3
"""Connection setup cost of per-call steering (W13 check).

The UPF decides once per NEW connection, so per-call steering needs one
connection per call. This measures, per site, the latency of one kv_get call
(a) on a new connection (what steering costs) and (b) on a kept-alive
connection (what a pinned client pays), from one UE through the UPF.
Usage (UE netns): python tools/conn_cost.py --src 10.60.0.1 --out results/conn_cost
"""
import argparse
import csv
import json
import socket
import statistics as st
import time

import httpx

SITES = {"mec1": "10.201.1.10", "mec2": "10.201.2.10", "mec3": "10.201.3.10"}
ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True)
ap.add_argument("-n", type=int, default=200)
ap.add_argument("--out", default="results/conn_cost")
a = ap.parse_args()
BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "kv_get", "arguments": {"key": "supplier:7"}}}
H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
OPT = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]


def pct(xs, p):
    xs = sorted(xs)
    return xs[max(0, min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1)))))]


def run(ip, keep):
    xs = []
    tr = httpx.HTTPTransport(local_address=a.src, socket_options=OPT)
    lim = httpx.Limits(max_keepalive_connections=1 if keep else 0)
    hdr = H if keep else H | {"Connection": "close"}
    with httpx.Client(transport=tr, headers=hdr, timeout=10, limits=lim) as c:
        c.post(f"http://{ip}:8001/mcp", json=BODY)          # warm-up (opens it when kept)
        for _ in range(a.n):
            t = time.perf_counter()
            c.post(f"http://{ip}:8001/mcp", json=BODY).raise_for_status()
            xs.append(1000 * (time.perf_counter() - t))
            time.sleep(0.005)
    return xs


rows = []
for site, ip in SITES.items():
    new, kept = run(ip, False), run(ip, True)
    r = {"site": site, "new_p50_ms": st.median(new), "new_p95_ms": pct(new, 95),
         "kept_p50_ms": st.median(kept), "kept_p95_ms": pct(kept, 95),
         "setup_cost_p50_ms": st.median(new) - st.median(kept)}
    rows.append({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    print(json.dumps(rows[-1]))
with open(a.out + ".csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
