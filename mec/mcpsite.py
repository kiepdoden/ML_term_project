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
Each one is (sum of remaining work of queued and running calls) / workers.
"""
import asyncio
import hashlib
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
                 "worker", "call_id", "inflight_at_arr", "pred_s")

    def __init__(self, tool, args, fn, call_id):
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
                 timeout=30.0, estimators=None, on_timeout=None):
        """run_fn_factory(i) -> resource handed to job.fn(resource, args)."""
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

    async def start(self):
        self.q = asyncio.Queue()
        for i in range(self.n):
            asyncio.create_task(self._worker(i))

    async def submit(self, tool, args, fn, call_id=None):
        job = Job(tool, args, fn, call_id)
        job.inflight_at_arr = len(self.queued) + len(self.running)
        if self.est.model is not None:
            # predict on arrival, for every call and every policy, so all
            # policies pay the same estimator cost (EXPLAIN + trees, a few ms)
            await asyncio.to_thread(self.est.pred_s, job)
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
            task = asyncio.ensure_future(asyncio.to_thread(job.fn, res, job.args))
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
            self.log.write(json.dumps({
                "site": self.site, "svc": self.svc, "id": job.id,
                "call_id": job.call_id, "tool": job.tool, "args": job.args,
                "key": job.key, "t_arr": job.t_arr, "t_start": job.t_start,
                "t_end": t_end, "wait_s": job.t_start - job.t_arr,
                "run_s": t_end - job.t_start, "worker": i,
                "inflight_at_arr": job.inflight_at_arr, "err": err,
                "pred_s": job.pred_s,
            }) + "\n")
            if not job.fut.done():
                if err:
                    job.fut.set_exception(RuntimeError(err))
                else:
                    job.fut.set_result(out)

    def _wait_ms(self, est_fn):
        now = time.time()
        work = sum(est_fn(j) for j in self.queued.values())
        work += sum(max(est_fn(j) - (now - j.t_start), 0.0) for j in self.running.values())
        return 1000.0 * work / self.n

    def status(self):
        return {
            "site": self.site, "svc": self.svc, "ts": time.time(),
            "workers": self.n, "queued": len(self.queued),
            "running": len(self.running),
            "inflight": len(self.queued) + len(self.running),
            "done": self.done,
            "wait_b2_ms": round(self._wait_ms(self.est.b2_s), 3),
            "wait_pred_ms": round(self._wait_ms(self.est.pred_s), 3),
            "wait_oracle_ms": round(self._wait_ms(self.est.oracle_s), 3),
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


def header(ctx, name):
    """Read an HTTP request header from an MCP tool context, if any."""
    try:
        req = ctx.request_context.request
        return req.headers.get(name) if req is not None else None
    except Exception:
        return None
