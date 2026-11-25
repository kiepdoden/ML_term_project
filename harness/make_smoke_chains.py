#!/usr/bin/env python3
"""Hand-made chains for a smoke test of the pipeline (NOT the recorded
workload): a mix of long scans and short lookups with 1 s think time.
run_s is filled from one isolated call per query on mec1."""
import json
import socket
import sys
import time

import httpx

LONG = [
    "SELECT l_returnflag, l_linestatus, sum(l_quantity), sum(l_extendedprice*(1-l_discount)) "
    "FROM lineitem WHERE l_shipdate <= DATE '1998-09-02' GROUP BY 1,2 ORDER BY 1,2",
    "SELECT n_name, sum(l_extendedprice*(1-l_discount)) rev FROM customer, orders, lineitem, "
    "supplier, nation, region WHERE c_custkey=o_custkey AND l_orderkey=o_orderkey AND "
    "l_suppkey=s_suppkey AND c_nationkey=s_nationkey AND s_nationkey=n_nationkey AND "
    "n_regionkey=r_regionkey AND r_name='ASIA' AND o_orderdate >= DATE '1994-01-01' AND "
    "o_orderdate < DATE '1995-01-01' GROUP BY n_name ORDER BY rev DESC",
]
SHORT = [
    "SELECT o_orderkey, o_totalprice FROM orders WHERE o_orderkey = 4242",
    "SELECT c_name, c_phone FROM customer WHERE c_custkey = 777",
]
HDR = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
       "Connection": "close"}


def iso(tool, args):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}
    port = 8001 if tool == "kv_get" else 8000
    t = time.time()
    tr = httpx.HTTPTransport(socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)])
    with httpx.Client(transport=tr, headers=HDR, timeout=60) as c:
        c.post(f"http://10.201.1.10:{port}/mcp", json=body).raise_for_status()
    return time.time() - t


steps_long = [("describe_table", {"table": "lineitem"}), ("run_sql", {"query": LONG[0]}),
              ("kv_get", {"key": "supplier:7"})]
steps_mixed = [("run_sql", {"query": SHORT[0]}), ("run_sql", {"query": SHORT[1]}),
               ("kv_get", {"key": "customer:777"}), ("run_sql", {"query": LONG[1]})]
steps_short = [("run_sql", {"query": SHORT[0]}), ("kv_get", {"key": "customer:42"}),
               ("run_sql", {"query": SHORT[1]})]
out = []
for name, steps, reps in [("long", steps_long, 1), ("mixed", steps_mixed, 2),
                          ("short", steps_short, 7)]:
    st = []
    for tool, args in steps:
        rs = min(iso(tool, args) for _ in range(2))
        st.append({"tool": tool, "args": args, "think_s": 1.0, "client_s": rs, "run_s": rs})
    for r in range(reps):
        out.append({"task": f"smoke-{name}", "template": "smoke", "run": r, "steps": st})
    print(name, [round(s["run_s"], 3) for s in st], file=sys.stderr)
with open(sys.argv[1], "w") as f:
    for c in out:
        f.write(json.dumps(c) + "\n")
