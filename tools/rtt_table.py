#!/usr/bin/env python3
"""RTT table: from one emulated UE, through the UPF, to each MEC site.

Run inside the UE network namespace, e.g.
  docker run --rm --network container:ueransim -v $PWD:/app mcp-steer:0.1 \
      python tools/rtt_table.py --src 10.60.0.1 --out results/rtt_table
For each site it measures, n times each:
  icmp      ping RTT
  tcp       TCP connect (SYN -> SYN/ACK)
  mcp_call  one MCP tools/call kv_get on a NEW connection (connect + request
            + 5 ms service + response), i.e. what one steered call pays
"""
import argparse
import csv
import json
import re
import socket
import statistics as st
import subprocess
import time

import httpx

SITES = {"mec1": "10.201.1.10", "mec2": "10.201.2.10", "mec3": "10.201.3.10"}
ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True, help="UE IP (selects its tunnel)")
ap.add_argument("--dev", default="uesimtun0")
ap.add_argument("-n", type=int, default=100)
ap.add_argument("--out", default="results/rtt_table")
a = ap.parse_args()

BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "kv_get", "arguments": {"key": "supplier:7"}}}
HDR = {"Content-Type": "application/json",
       "Accept": "application/json, text/event-stream", "Connection": "close"}


def pct(xs, p):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1)))))
    return xs[k]


def icmp(ip):
    out = subprocess.run(["ping", "-c", str(a.n), "-i", "0.05", "-W", "2", "-I", a.dev, ip],
                         capture_output=True, text=True).stdout
    return [float(x) for x in re.findall(r"time=([\d.]+) ms", out)]


def tcp(ip, port):
    xs = []
    for _ in range(a.n):
        s = socket.socket()
        s.bind((a.src, 0))
        t = time.perf_counter()
        s.connect((ip, port))
        xs.append(1000 * (time.perf_counter() - t))
        s.close()
        time.sleep(0.01)
    return xs


def mcp_call(ip, port):
    xs = []
    # TCP_NODELAY: without it, Nagle + delayed ACK add ~40 ms to a request
    # whose headers and body leave in two writes.
    tr = httpx.HTTPTransport(local_address=a.src, socket_options=[
        (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)])
    with httpx.Client(transport=tr, headers=HDR, timeout=10) as c:
        for _ in range(a.n):
            t = time.perf_counter()
            r = c.post(f"http://{ip}:{port}/mcp", json=BODY)
            r.raise_for_status()
            xs.append(1000 * (time.perf_counter() - t))
            time.sleep(0.01)
    return xs


rows = []
for site, ip in SITES.items():
    m = {"icmp": icmp(ip), "tcp": tcp(ip, 8001), "mcp_call": mcp_call(ip, 8001)}
    row = {"site": site, "ip": ip}
    for k, xs in m.items():
        row[f"{k}_p50_ms"] = round(st.median(xs), 2)
        row[f"{k}_p95_ms"] = round(pct(xs, 95), 2)
        row[f"{k}_n"] = len(xs)
    rows.append(row)
    print(json.dumps(row))

with open(a.out + ".csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
with open(a.out + ".md", "w") as f:
    f.write("| Site | ICMP RTT p50 (p95) ms | TCP connect p50 (p95) ms | "
            "MCP call, new connection p50 (p95) ms |\n|---|---|---|---|\n")
    for r in rows:
        f.write(f"| {r['site']} | {r['icmp_p50_ms']} ({r['icmp_p95_ms']}) | "
                f"{r['tcp_p50_ms']} ({r['tcp_p95_ms']}) | "
                f"{r['mcp_call_p50_ms']} ({r['mcp_call_p95_ms']}) |\n")
print(open(a.out + ".md").read())
