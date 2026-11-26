#!/usr/bin/env python3
"""Steering controller. Runs on the UPF host (root, for nsenter).

Every `period` seconds it reads the latest status datagram of each replica
(UDP, pushed every 100 ms) and, per MCP service, sets how the NEW connections
of the next interval are split over the replicas: the UPF draws a random
bucket 0-99 per new connection and the split map sends the bucket to a
replica. One call = one connection = one draw.

Policies (same code, only the signal changes):
  b0      nearest replica only                              (today's anycast)
  b1      in-flight call count, ties to the nearest         (CATS-style)
  b2      wait from per-tool mean run time + k x RTT        (no arguments)
  method  wait predicted from the arguments + k x RTT
  method2 as method, plus the work local (unsteered) users of each site are
          expected to bring in the next `horizon` s               (method v2)
  oracle  wait from the recorded run time + k x RTT         (ablation)
Split:
  waterfill (default)  give the calls expected in the next `horizon` seconds
            to the replicas with the lowest signal, filling them up to one
            common level (water-filling). With few arrivals this is the argmin;
            with many it spreads them, so one stale view is not followed by a
            herd of calls to a single replica. B1 fills call counts, the wait
            policies fill seconds of work.
  argmin    every call of the interval goes to the best replica.
Freshness contract: a status older than `stale` seconds is ignored; if no
replica of a service is fresh, the service falls back to the nearest one.
"""
import argparse
import collections
import json
import os
import socket
import subprocess
import time

SITES = ["mec1", "mec2", "mec3"]          # ordered nearest first
REPLICA = {"mec1": "10.201.1.10", "mec2": "10.201.2.10", "mec3": "10.201.3.10"}
SVC = ["sql", "kv"]
WAIT_FIELD = {"b2": "wait_b2_ms", "method": "wait_pred_ms", "method2": "wait_pred2_ms",
              "oracle": "wait_oracle_ms"}
WORK_FIELD = {"b2": "b2", "method": "pred", "method2": "pred", "oracle": "oracle"}

ap = argparse.ArgumentParser()
ap.add_argument("--policy", choices=["b0", "b1", "b2", "method", "method2", "oracle"], required=True)
ap.add_argument("--split", choices=["waterfill", "argmin"], default="waterfill")
ap.add_argument("--period", type=float, default=0.1)
ap.add_argument("--horizon", type=float, default=0.2,
                help="seconds of arrivals one split decision covers (period + report delay)")
ap.add_argument("--stale", type=float, default=0.3)
ap.add_argument("--rtt", default="mec1=1.65,mec2=11.85,mec3=29.7",
                help="measured UE-to-site RTT in ms (RTT table)")
ap.add_argument("--k-rtt", type=float, default=2.0,
                help="round trips one call pays (TCP connect + request)")
ap.add_argument("--listen", default="0.0.0.0:9999")
ap.add_argument("--upf", default="upf", help="UPF container name")
ap.add_argument("--log", default="results/controller.jsonl")
ap.add_argument("--duration", type=float, default=0.0, help="0 = run forever")
a = ap.parse_args()
RTT = {kv.split("=")[0]: float(kv.split("=")[1]) for kv in a.rtt.split(",")}

pid = subprocess.check_output(["docker", "inspect", "-f", "{{.State.Pid}}", a.upf],
                              text=True).strip()


def buckets(weights):
    """weights {site: w} -> 100 buckets by largest remainder, in site order."""
    tot = sum(weights.values())
    raw = {s: 100.0 * w / tot for s, w in weights.items() if w > 0}
    n = {s: int(v) for s, v in raw.items()}
    for s in sorted(raw, key=lambda s: raw[s] - n[s], reverse=True)[: 100 - sum(n.values())]:
        n[s] += 1
    return tuple((s, n[s]) for s in SITES if n.get(s, 0) > 0)


def nft_set(svc, bk):
    lo, parts = 0, []
    for s, k in bk:
        parts.append(f"{lo}-{lo + k - 1} : {REPLICA[s]}")
        lo += k
    rule = (f"flush map ip steer {svc}_split\n"
            f"add element ip steer {svc}_split {{ {', '.join(parts)} }}\n")
    subprocess.run(["nsenter", "-t", pid, "-n", "nft", "-f", "-"], input=rule,
                   text=True, check=True)


def waterfill(level, cap, amount):
    """Fill replicas from the lowest level up to one common level L with
    `amount`; cap[s] = amount that raises s by one level unit."""
    order = sorted(level, key=lambda s: (level[s], SITES.index(s)))
    if amount <= 1e-9:
        return {order[0]: 1.0}
    for k in range(1, len(order) + 1):
        top = order[:k]
        L = (amount + sum(cap[s] * level[s] for s in top)) / sum(cap[s] for s in top)
        if k == len(order) or L <= level[order[k]]:
            break
    return {s: cap[s] * max(0.0, L - level[s]) / amount for s in top}


hist = {svc: collections.deque() for svc in SVC}   # (t, arrived, work) per service


def rates(svc, latest, now):
    """Arrival rate (calls/s and work s/s) over ~1 s, from cumulative counters."""
    st = latest.get(svc, {})
    if not st:
        return 0.0, 0.0
    field = WORK_FIELD.get(a.policy, "b2")
    arr = sum(v[0].get("arrived", 0) for v in st.values())
    work = sum(v[0].get("arr_work_s", {}).get(field, 0.0) for v in st.values())
    h = hist[svc]
    h.append((now, arr, work))
    while len(h) > 2 and now - h[0][0] > 1.0:
        h.popleft()
    t0, a0, w0 = h[0]
    dt = now - t0
    if dt < 0.3 or arr < a0:               # too short, or a replica restarted
        return 0.0, 0.0
    return (arr - a0) / dt, max(0.0, work - w0) / dt


def choose(svc, latest, now):
    fresh = {s: st for s, (st, t) in latest.get(svc, {}).items() if now - t <= a.stale}
    if a.policy == "b0" or not fresh:
        return {SITES[0]: 1.0}, "nearest" if a.policy == "b0" else "fallback"
    r_calls, r_work = rates(svc, latest, now)
    if a.policy == "b1":
        level = {s: float(fresh[s]["inflight"]) for s in fresh}
        cap = {s: 1.0 for s in fresh}
        amount = r_calls * a.horizon
    else:
        f = WAIT_FIELD[a.policy]
        level = {s: fresh[s][f] + a.k_rtt * RTT[s] for s in fresh}          # ms
        cap = {s: fresh[s]["workers"] / 1000.0 for s in fresh}              # work s per ms
        amount = r_work * a.horizon
    if a.split == "argmin":
        amount = 0.0
    w = waterfill(level, cap, amount)
    return w, {"level": {s: round(v, 1) for s, v in level.items()}, "amount": round(amount, 3)}


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
host, port = a.listen.rsplit(":", 1)
sock.bind((host, int(port)))
sock.setblocking(False)
os.makedirs(os.path.dirname(a.log) or ".", exist_ok=True)
log = open(a.log, "a", buffering=1)

latest = {}                                  # svc -> site -> (status, recv time)
current = {}
for svc in SVC:                              # every run starts from the nearest
    current[svc] = buckets({SITES[0]: 1.0})
    nft_set(svc, current[svc])
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
        w, why = choose(svc, latest, now)
        bk = buckets(w)
        if bk != current[svc]:
            nft_set(svc, bk)
            current[svc] = bk
            log.write(json.dumps({"ts": now, "svc": svc, "split": dict(bk), "why": why,
                                  "policy": a.policy}) + "\n")
    if now - last_snap >= 1.0:               # one snapshot per second
        last_snap = now
        snap = {svc: {s: {k: st.get(k) for k in ("inflight", "wait_b2_ms", "wait_pred_ms",
                                                  "wait_pred2_ms", "wait_oracle_ms",
                                                  "local_rate", "calib")}
                      | {"age_ms": round(1000 * (now - t), 1)}
                      for s, (st, t) in latest.get(svc, {}).items()} for svc in SVC}
        log.write(json.dumps({"ts": now, "snapshot": snap,
                              "current": {k: dict(v) for k, v in current.items()},
                              "policy": a.policy, "split": a.split}) + "\n")
    if a.duration and now - t0 >= a.duration:
        break
    next_tick += a.period
    time.sleep(max(0.0, next_tick - time.time()))
