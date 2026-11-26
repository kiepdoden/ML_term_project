"""Shared parts of one MCP replica at one MEC site.

A replica has a fixed number of workers that serve calls in FIFO order.
Every call is logged with its arguments, queue wait and run time, and the
replica pushes a status datagram (in-flight calls and wait estimates) to the
controller every `period` seconds.

Wait estimates sent in each status datagram:
  inflight          number of queued + running calls            (B1)
  wait_b2_ms        per-tool mean run time, no arguments        (B2)
  wait_pred_ms      per-call prediction from the arguments      (method)
  wait_oracle_ms    run time measured offline for that call     (ablation)
  wait_pred2_ms     as wait_pred_ms, plus the work that local users are
                    expected to bring in the next `horizon` s     (method v2)
Each one is the time until a worker is free for a new call, replaying the
FIFO queue on the workers with that estimator's run times.

Local users are callers that the UPF does not steer: they address the replica
itself, not the anycast address, so the HTTP Host header tells them apart
(the UPF rewrites the destination address, not the Host header).
"""
import asyncio
import collections
import hashlib
import heapq
import json
import os
import socket
import time
import uuid


def call_key(tool, args):
    """Stable key of one call: tool name + canonical JSON of its arguments."""
    blob = tool + "|" + json.dumps(args, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


class Job:
    __slots__ = ("id", "tool", "args", "key", "fn", "fut", "t_arr", "t_start",
                 "worker", "call_id", "inflight_at_arr", "pred_s", "local")

    def __init__(self, tool, args, fn, call_id, local=False):
        self.id = uuid.uuid4().hex[:12]
        self.tool = tool
        self.args = args
        self.key = call_key(tool, args)
        self.fn = fn
        self.fut = asyncio.get_running_loop().create_future()
        self.t_arr = time.time()
        self.t_start = None
        self.worker = None
        self.call_id = call_id
        self.inflight_at_arr = 0
        self.pred_s = None
        self.local = local


class Estimators:
    """Run-time estimates used by the wait estimates.

    b2:     {tool: mean seconds}                      (json file, optional)
    oracle: {call_key: seconds measured offline}      (json file, optional)
    model:  callable(tool, args) -> seconds           (set later, method v1)
    """

    def __init__(self, b2_path=None, oracle_path=None):
        self.b2 = self._load(b2_path)
        self.oracle = self._load(oracle_path)
        self.model = None

    @staticmethod
    def _load(path):
        if path and os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}

    def b2_s(self, job):
        return float(self.b2.get(job.tool, self.b2.get("_default", 0.0)))

    def oracle_s(self, job):
        v = self.oracle.get(job.key)
        return float(v) if v is not None else self.b2_s(job)

    def pred_s(self, job):
        if self.model is None:
            return self.b2_s(job)
        if job.pred_s is None:
            try:
                job.pred_s = max(0.0, float(self.model(job.tool, job.args)))
            except Exception:
                job.pred_s = self.b2_s(job)
        return job.pred_s


class Pool:
    def __init__(self, site, svc, workers, run_fn_factory, log_path,
                 timeout=30.0, estimators=None, on_timeout=None, pin=False,
                 resid_frac=float(os.environ.get("RESID_FRAC", "0.5"))):
        """run_fn_factory(i) -> resource handed to job.fn(resource, args)."""
        self.pin = pin
        self.resid_frac = resid_frac
        self.calib = {"b2": 1.0, "pred": 1.0, "oracle": 1.0}
        self.arrived = 0
        self.arr_work = {"b2": 0.0, "pred": 0.0, "oracle": 0.0}
        self.calib_alpha = float(os.environ.get("CALIB_ALPHA", "0.1"))
        # method v2: work brought by local (unsteered) users, as a rate over
        # the last `local_win` s, expected over the controller's horizon
        self.local_arr = collections.deque()          # (arrival time, est. work s)
        self.local_win = float(os.environ.get("LOCAL_WIN", "10"))
        self.horizon = float(os.environ.get("STEER_HORIZON", "0.2"))
        self.work_cpus = None
        self.site = site
        self.svc = svc
        self.n = workers
        self.res = [run_fn_factory(i) for i in range(workers)]
        self.timeout = timeout
        self.on_timeout = on_timeout
        self.est = estimators or Estimators()
        self.queued = {}
        self.running = {}
        self.q = None
        self.done = 0
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self.log = open(log_path, "a", buffering=1)

    def _plan_cpus(self):
        """One worker per physical core (first hardware thread of each core);
        the event loop and the estimator get the other threads. Without this,
        two workers can share one core and long calls run ~35% slower."""
        try:
            cpus = sorted(os.sched_getaffinity(0))
            cores = {}
            for c in cpus:
                with open(f"/sys/devices/system/cpu/cpu{c}/topology/core_id") as fh:
                    cores.setdefault(int(fh.read()), []).append(c)
            firsts = [v[0] for _, v in sorted(cores.items())]
            if len(firsts) < self.n:
                return None, None
            work = firsts[: self.n]
            rest = [c for c in cpus if c not in work] or cpus
            return work, rest
        except (OSError, AttributeError, ValueError):
            return None, None

    async def start(self):
        self.q = asyncio.Queue()
        self.work_cpus, rest = self._plan_cpus() if self.pin else (None, None)
        if rest:
            os.sched_setaffinity(0, rest)     # this (main) thread and threads it starts
        for i in range(self.n):
            asyncio.create_task(self._worker(i))

    async def submit(self, tool, args, fn, call_id=None, local=False):
        job = Job(tool, args, fn, call_id, local)
        job.inflight_at_arr = len(self.queued) + len(self.running)
        if self.est.model is not None:
            # predict on arrival, for every call and every policy, so all
            # policies pay the same estimator cost (EXPLAIN + trees, a few ms)
            await asyncio.to_thread(self.est.pred_s, job)
        # arrivals and their estimated work: the controller turns these into
        # an arrival rate to split the next interval's calls (water-filling)
        self.arrived += 1
        self.arr_work["b2"] += self.calib["b2"] * self.est.b2_s(job)
        self.arr_work["pred"] += self.calib["pred"] * self.est.pred_s(job)
        self.arr_work["oracle"] += self.calib["oracle"] * self.est.oracle_s(job)
        if local:
            self.local_arr.append((job.t_arr, self.calib["pred"] * self.est.pred_s(job)))
        self.queued[job.id] = job
        await self.q.put(job)
        return await job.fut

    async def _worker(self, i):
        res = self.res[i]
        while True:
            job = await self.q.get()
            self.queued.pop(job.id, None)
            job.t_start = time.time()
            job.worker = i
            self.running[job.id] = job
            task = asyncio.ensure_future(asyncio.to_thread(self._run_pinned, i, job.fn, res, job.args))
            done, _ = await asyncio.wait({task}, timeout=self.timeout)
            if not done and self.on_timeout:
                self.on_timeout(res)
            err = None
            out = None
            try:
                out = await task
            except Exception as e:  # noqa: BLE001 - report every failure to the client
                err = f"{type(e).__name__}: {e}"[:300]
            t_end = time.time()
            self.running.pop(job.id, None)
            self.done += 1
            self._calibrate(job, t_end - job.t_start)
            self.log.write(json.dumps({
                "site": self.site, "svc": self.svc, "id": job.id,
                "call_id": job.call_id, "tool": job.tool, "args": job.args,
                "key": job.key, "t_arr": job.t_arr, "t_start": job.t_start,
                "t_end": t_end, "wait_s": job.t_start - job.t_arr,
                "run_s": t_end - job.t_start, "worker": i,
                "inflight_at_arr": job.inflight_at_arr, "err": err,
                "pred_s": job.pred_s, "local": job.local,
            }) + "\n")
            if not job.fut.done():
                if err:
                    job.fut.set_exception(RuntimeError(err))
                else:
                    job.fut.set_result(out)

    def _run_pinned(self, i, fn, res, args):
        if self.work_cpus:
            os.sched_setaffinity(0, {self.work_cpus[i]})
        return fn(res, args)

    def _calibrate(self, job, run_s):
        """Online calibration (the W7 twin idea): run times at this site drift
        from the offline estimates (e.g. ~1.4x slower when all workers are
        busy). Keep an EWMA of measured / estimated run time per estimator,
        over calls estimated at >= 50 ms, and scale every estimate by it.
        B2, the method and the oracle get the same treatment."""
        for name, fn in (("b2", self.est.b2_s), ("pred", self.est.pred_s),
                         ("oracle", self.est.oracle_s)):
            if name == "pred" and job.pred_s is None and self.est.model is not None:
                continue
            e = fn(job)
            if e >= 0.05:
                r = min(5.0, max(0.2, run_s / e))
                self.calib[name] = (1 - self.calib_alpha) * self.calib[name] + self.calib_alpha * r

    def local_rate(self):
        """Estimated work (s) that local users bring per second, over the last
        `local_win` seconds."""
        cut = time.time() - self.local_win
        while self.local_arr and self.local_arr[0][0] < cut:
            self.local_arr.popleft()
        return sum(w for _, w in self.local_arr) / self.local_win

    def _wait_ms(self, est_fn, name, ahead_s=0.0):
        """How long a NEW call would wait here: replay the FIFO queue on the
        workers. Each worker is free after the remaining time of its running
        call (0 if idle); queued calls take the earliest free worker in order;
        the new call waits until the earliest worker after that. (Remaining
        work / workers, the fluid view, is wrong with two workers: one long
        call plus one idle worker means no wait at all.) A running call that
        has outlived its estimate is not counted as done: run times are
        heavy-tailed, so its expected remaining time grows with its age.
        `ahead_s`: expected work that arrives before the new call (method v2:
        local users over the horizon), queued last, as one call."""
        now = time.time()
        k = self.calib[name]
        free = [max(k * est_fn(j) - (now - j.t_start), self.resid_frac * (now - j.t_start))
                for j in self.running.values()]
        free += [0.0] * max(0, self.n - len(free))
        heapq.heapify(free)
        for j in self.queued.values():               # dicts keep arrival order
            heapq.heappush(free, heapq.heappop(free) + k * est_fn(j))
        if ahead_s > 0:
            heapq.heappush(free, heapq.heappop(free) + ahead_s)
        return 1000.0 * free[0]

    def status(self):
        return {
            "site": self.site, "svc": self.svc, "ts": time.time(),
            "workers": self.n, "queued": len(self.queued),
            "running": len(self.running),
            "inflight": len(self.queued) + len(self.running),
            "done": self.done,
            "wait_b2_ms": round(self._wait_ms(self.est.b2_s, "b2"), 3),
            "wait_pred_ms": round(self._wait_ms(self.est.pred_s, "pred"), 3),
            "wait_oracle_ms": round(self._wait_ms(self.est.oracle_s, "oracle"), 3),
            "wait_pred2_ms": round(self._wait_ms(self.est.pred_s, "pred",
                                                 self.local_rate() * self.horizon), 3),
            "local_rate": round(self.local_rate(), 4),
            "calib": {k: round(v, 3) for k, v in self.calib.items()},
            "arrived": self.arrived,
            "arr_work_s": {k: round(v, 4) for k, v in self.arr_work.items()},
        }

    async def pusher(self, controller, period):
        """Send status to controller ("host:port", UDP) every `period` s."""
        if not controller:
            return
        host, port = controller.rsplit(":", 1)
        addr = (host, int(port))
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        while True:
            try:
                sock.sendto(json.dumps(self.status()).encode(), addr)
            except OSError:
                pass
            await asyncio.sleep(period)


def quiet_mcp_logging():
    """MCP SDK 1.20 in stateless mode logs a harmless ClosedResourceError at
    the end of every request, and its rich log handler renders the traceback
    with syntax highlighting: about 0.1 s of CPU per call, 20x the 5 ms tool
    itself. Silence those loggers so the server spends CPU on the tools."""
    import logging
    for name in ("mcp", "mcp.server", "mcp.server.streamable_http",
                 "mcp.server.streamable_http_manager", "mcp.server.lowlevel.server"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.CRITICAL)
        lg.handlers[:] = []
        lg.propagate = False
    logging.getLogger().handlers[:] = [logging.StreamHandler()]
    logging.getLogger().setLevel(logging.WARNING)


ANYCAST_PREFIX = os.environ.get("ANYCAST_PREFIX", "10.200.0.")


def is_local(ctx):
    """True for a caller that addressed this replica directly (a local user),
    False for a call that came to the anycast address (steered by the UPF)."""
    host = header(ctx, "host")
    return bool(host) and not host.startswith(ANYCAST_PREFIX)


def header(ctx, name):
    """Read an HTTP request header from an MCP tool context, if any."""
    try:
        req = ctx.request_context.request
        return req.headers.get(name) if req is not None else None
    except Exception:
        return None
