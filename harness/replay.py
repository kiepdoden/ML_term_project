#!/usr/bin/env python3
"""Replay recorded MCP call chains from emulated UEs (the workload).

Runs inside the UERANSIM network namespace:
  docker run --rm --cpuset-cpus 0,1,8,9 --network container:ueransim \
      -v $PWD:/app mcp-steer:0.1 python harness/replay.py ...
Each logical client is bound to one UE tunnel IP and loops:
  pick a recorded task run (seeded) -> for each step: wait the recorded
  LLM think time (x think_scale), send the same tool call to the tool's
  ANYCAST address on a new connection, wait for the response -> idle gap.
The UPF's map decides the replica per new connection. The idle gap is set
so that the offered work is `load` x the total worker capacity of the
three sites (from the recorded run times).
Per call it writes: call_id, client, task, step, tool, t_send, t_recv,
latency_s, ok. Which site served the call is joined from the replica logs.
"""
import argparse
import asyncio
import json
import os
import random
import socket
import time
import uuid

import httpx

ANY = {"sql": "http://10.200.0.10:8000/mcp", "kv": "http://10.200.0.11:8001/mcp"}
SVC = {"list_tables": "sql", "describe_table": "sql", "run_sql": "sql", "kv_get": "kv"}
HDR = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
       "Connection": "close"}

ap = argparse.ArgumentParser()
ap.add_argument("--chains", required=True, help="chains with run_s joined (jsonl)")
ap.add_argument("--tasks", default="", help="file with task ids to use (test split)")
ap.add_argument("--ue-ips", default="", help="comma list; default: all uesimtun IPs")
ap.add_argument("--clients", type=int, default=30)
ap.add_argument("--load", type=float, default=0.7, help="offered work / total capacity")
ap.add_argument("--capacity", type=float, default=6.0, help="worker-seconds per second")
ap.add_argument("--think-scale", type=float, default=1.0)
ap.add_argument("--duration", type=float, default=300.0)
ap.add_argument("--warmup", type=float, default=30.0)
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--timeout", type=float, default=60.0)
ap.add_argument("--bg-site", default="10.201.1.10",
                help="replica that gets the unsteerable background load (skew)")
ap.add_argument("--bg-load", type=float, default=0.0,
                help="background work as a fraction of that site's capacity")
ap.add_argument("--bg-workers", type=float, default=2.0)
ap.add_argument("--bg-min-run", type=float, default=0.2,
                help="background calls are recorded run_sql calls at least this long")
ap.add_argument("--out", required=True)
a = ap.parse_args()


def ue_ips():
    if a.ue_ips:
        return a.ue_ips.split(",")
    import subprocess
    out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True).stdout
    ips = [l.split()[3].split("/")[0] for l in out.splitlines() if "uesimtun" in l]
    return sorted(ips, key=lambda s: tuple(int(x) for x in s.split(".")))


chains = [json.loads(l) for l in open(a.chains)]
if a.tasks:
    keep = set(open(a.tasks).read().split())
    chains = [c for c in chains if c["task"] in keep]
chains = [c for c in chains if c["steps"]]
assert chains, "no chains"

# offered load: per chain, work = sum of isolated run times, busy time = think + calls
work = [sum(s.get("run_s", 0.0) for s in c["steps"]) for c in chains]
span = [sum(a.think_scale * s["think_s"] + s.get("client_s", 0.0) for s in c["steps"])
        for c in chains]
mean_work = sum(work) / len(work)
mean_span = sum(span) / len(span)
# clients * mean_work / (mean_span + idle) = load * capacity
idle = max(0.0, a.clients * mean_work / (a.load * a.capacity) - mean_span)
max_load = a.clients * mean_work / mean_span / a.capacity
meta = {"chains": len(chains), "mean_work_s": mean_work, "mean_span_s": mean_span,
        "idle_mean_s": idle, "max_reachable_load": max_load, **vars(a)}
print(json.dumps(meta), flush=True)
os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
with open(a.out + ".meta.json", "w") as f:
    json.dump(meta, f, indent=1)

out = open(a.out, "w", buffering=1)
T_END = time.time() + a.duration
T_MEAS = time.time() + a.warmup


async def call(client, tool, args, call_id):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}
    r = await client.post(ANY[SVC[tool]], json=body, headers={"X-Call-Id": call_id})
    r.raise_for_status()
    res = r.json()
    return "error" not in res and not res.get("result", {}).get("isError", False)


async def client_loop(k, ip, rng):
    tr = httpx.AsyncHTTPTransport(local_address=ip, socket_options=[
        (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)])
    limits = httpx.Limits(max_keepalive_connections=0)
    async with httpx.AsyncClient(transport=tr, headers=HDR, timeout=a.timeout,
                                 limits=limits) as cl:
        await asyncio.sleep(rng.uniform(0, max(idle, 1.0)))     # spread the starts
        while time.time() < T_END:
            c = rng.choice(chains)
            tid = uuid.uuid4().hex[:10]
            for i, s in enumerate(c["steps"]):
                await asyncio.sleep(a.think_scale * s["think_s"])
                if time.time() >= T_END:
                    break
                cid = uuid.uuid4().hex[:16]
                t0 = time.time()
                try:
                    ok = await call(cl, s["tool"], s["args"], cid)
                    err = None
                except Exception as e:  # noqa: BLE001
                    ok, err = False, f"{type(e).__name__}"
                t1 = time.time()
                out.write(json.dumps({
                    "call_id": cid, "client": k, "ue": ip, "task_run": tid,
                    "task": c["task"], "run": c["run"], "step": i, "tool": s["tool"],
                    "iso_run_s": s.get("run_s"), "t_send": t0, "t_recv": t1,
                    "latency_s": t1 - t0, "ok": ok, "err": err,
                    "measured": t0 >= T_MEAS}) + "\n")
            await asyncio.sleep(rng.expovariate(1.0 / idle) if idle > 0 else 0)


async def background(ip, rng):
    """Skew: long scans sent straight to one replica (local users of that
    site that the UPF does not steer). Poisson arrivals at bg_load of its
    capacity. Logged with bg=true and left out of the metrics."""
    longs = [s for c in chains for s in c["steps"]
             if s["tool"] == "run_sql" and (s.get("run_s") or 0) >= a.bg_min_run]
    if not longs or a.bg_load <= 0:
        return
    mean_run = sum(s["run_s"] for s in longs) / len(longs)
    rate = a.bg_load * a.bg_workers / mean_run           # calls per second
    url = f"http://{a.bg_site}:8000/mcp"
    tr = httpx.AsyncHTTPTransport(local_address=ip, socket_options=[
        (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)])
    async with httpx.AsyncClient(transport=tr, headers=HDR, timeout=a.timeout,
                                 limits=httpx.Limits(max_keepalive_connections=0)) as cl:
        async def one(s):
            cid = uuid.uuid4().hex[:16]
            t0 = time.time()
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "run_sql", "arguments": s["args"]}}
            try:
                r = await cl.post(url, json=body, headers={"X-Call-Id": cid})
                ok = r.status_code == 200
            except Exception:  # noqa: BLE001
                ok = False
            out.write(json.dumps({"call_id": cid, "bg": True, "tool": "run_sql",
                                  "iso_run_s": s["run_s"], "t_send": t0,
                                  "t_recv": time.time(), "ok": ok,
                                  "measured": False}) + "\n")
        pending = set()
        while time.time() < T_END:
            await asyncio.sleep(rng.expovariate(rate))
            t = asyncio.create_task(one(rng.choice(longs)))
            pending.add(t)
            t.add_done_callback(pending.discard)


async def main():
    ips = ue_ips()
    rng0 = random.Random(a.seed)
    tasks = [asyncio.create_task(background(ips[-1], random.Random(a.seed + 999)))]
    for k in range(a.clients):
        tasks.append(asyncio.create_task(
            client_loop(k, ips[k % len(ips)], random.Random(rng0.random()))))
    await asyncio.gather(*tasks)


asyncio.run(main())
