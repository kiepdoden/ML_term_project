#!/usr/bin/env python3
"""Steering controller. Runs on the UPF host (root, for nsenter).

Every `period` seconds it reads the latest status datagram of each replica
(UDP, pushed by the replicas every 100 ms), picks one replica per MCP service
and, if the choice changed, rewrites that service's entry in the UPF's nft map
(one atomic nft transaction). The map is read only for NEW connections, so
one call = one connection = one decision.

Policies (same code, only the score changes):
  b0      nearest replica, never changes                 (today's anycast)
  b1      fewest in-flight calls, ties to the nearest    (CATS-style)
  b2      wait from per-tool mean run time + k * RTT     (no arguments)
  method  wait predicted from the arguments + k * RTT
  oracle  wait from the measured run time + k * RTT     (ablation upper bound)
Freshness contract: a status older than `stale` seconds is ignored; if no
replica of a service is fresh, the service falls back to the nearest one.
"""
import argparse
import json
import os
import socket
import subprocess
import time

SITES = ["mec1", "mec2", "mec3"]          # ordered nearest first
REPLICA = {"mec1": "10.201.1.10", "mec2": "10.201.2.10", "mec3": "10.201.3.10"}
SVC = {"sql": ("10.200.0.10", 8000), "kv": ("10.200.0.11", 8001)}
WAIT_FIELD = {"b2": "wait_b2_ms", "method": "wait_pred_ms", "oracle": "wait_oracle_ms"}

ap = argparse.ArgumentParser()
ap.add_argument("--policy", choices=["b0", "b1", "b2", "method", "oracle"], required=True)
ap.add_argument("--period", type=float, default=0.1)
ap.add_argument("--stale", type=float, default=0.3)
ap.add_argument("--rtt", default="mec1=1.65,mec2=11.85,mec3=29.7",
                help="measured UE-to-site RTT in ms (RTT table)")
ap.add_argument("--k-rtt", type=float, default=2.0,
                help="round trips one call pays (TCP connect + request)")
ap.add_argument("--eps-ms", type=float, default=0.0,
                help="switch only if the new choice is better by more than this")
ap.add_argument("--listen", default="0.0.0.0:9999")
ap.add_argument("--upf", default="upf", help="UPF container name")
ap.add_argument("--log", default="results/controller.jsonl")
ap.add_argument("--duration", type=float, default=0.0, help="0 = run forever")
a = ap.parse_args()
RTT = {kv.split("=")[0]: float(kv.split("=")[1]) for kv in a.rtt.split(",")}

pid = subprocess.check_output(["docker", "inspect", "-f", "{{.State.Pid}}", a.upf],
                              text=True).strip()


def nft_set(svc, site):
    ip, port = SVC[svc]
    rule = (f"delete element ip steer svc {{ {ip} . {port} }}\n"
            f"add element ip steer svc {{ {ip} . {port} : {REPLICA[site]} . {port} }}\n")
    subprocess.run(["nsenter", "-t", pid, "-n", "nft", "-f", "-"], input=rule,
                   text=True, check=True)


def choose(svc, latest, now):
    fresh = {s: st for s, (st, t) in latest.get(svc, {}).items()
             if now - t <= a.stale}
    if a.policy == "b0" or not fresh:
        return SITES[0], "nearest" if a.policy == "b0" else "fallback"
    if a.policy == "b1":
        best = min(fresh, key=lambda s: (fresh[s]["inflight"], SITES.index(s)))
        return best, f"inflight={fresh[best]['inflight']}"
    f = WAIT_FIELD[a.policy]
    score = {s: fresh[s][f] + a.k_rtt * RTT[s] for s in fresh}
    best = min(score, key=lambda s: (score[s], SITES.index(s)))
    return best, f"score_ms={score[best]:.1f}"


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
host, port = a.listen.rsplit(":", 1)
sock.bind((host, int(port)))
sock.setblocking(False)
os.makedirs(os.path.dirname(a.log) or ".", exist_ok=True)
log = open(a.log, "a", buffering=1)

latest = {}                                  # svc -> site -> (status, recv time)
current = {}
for svc in SVC:                              # every run starts from the nearest
    nft_set(svc, SITES[0])
    current[svc] = SITES[0]
t0 = time.time()
next_tick = t0
last_snap = 0.0
while True:
    while True:                              # drain all pending datagrams
        try:
            data, _ = sock.recvfrom(65535)
        except BlockingIOError:
            break
        try:
            st = json.loads(data)
            latest.setdefault(st["svc"], {})[st["site"]] = (st, time.time())
        except (ValueError, KeyError):
            pass
    now = time.time()
    for svc in SVC:
        site, why = choose(svc, latest, now)
        cur = current[svc]
        if site != cur and a.policy in ("b2", "method", "oracle") and a.eps_ms > 0:
            fresh_cur = latest.get(svc, {}).get(cur)
            if fresh_cur and now - fresh_cur[1] <= a.stale:
                f = WAIT_FIELD[a.policy]
                gain = (fresh_cur[0][f] + a.k_rtt * RTT[cur]) - \
                       (latest[svc][site][0][f] + a.k_rtt * RTT[site])
                if gain <= a.eps_ms:
                    site = cur
        if site != cur:
            nft_set(svc, site)
            current[svc] = site
            log.write(json.dumps({"ts": now, "svc": svc, "to": site, "from": cur,
                                  "why": why, "policy": a.policy}) + "\n")
    if now - last_snap >= 1.0:               # one snapshot per second
        last_snap = now
        snap = {svc: {s: {k: st[k] for k in ("inflight", "wait_b2_ms", "wait_pred_ms",
                                              "wait_oracle_ms")} | {"age_ms": round(1000 * (now - t), 1)}
                      for s, (st, t) in latest.get(svc, {}).items()} for svc in SVC}
        log.write(json.dumps({"ts": now, "snapshot": snap, "current": current,
                              "policy": a.policy}) + "\n")
    if a.duration and now - t0 >= a.duration:
        break
    next_tick += a.period
    time.sleep(max(0.0, next_tick - time.time()))
