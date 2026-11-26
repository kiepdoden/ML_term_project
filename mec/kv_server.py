#!/usr/bin/env python3
"""MCP key-value lookup server: the control tool with a nearly constant run
time (about 5 ms per call). Keys: customer:<custkey>, supplier:<suppkey>."""
import argparse
import asyncio
import json
import os
import time

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcpsite import Estimators, Pool, header, is_local, quiet_mcp_logging

ap = argparse.ArgumentParser()
ap.add_argument("--site", default=os.environ.get("SITE", "mec1"))
ap.add_argument("--data", default="/data")
ap.add_argument("--workers", type=int, default=16)
ap.add_argument("--port", type=int, default=8001)
ap.add_argument("--log", default="/var/log/mcp/kv_calls.jsonl")
ap.add_argument("--controller", default=os.environ.get("CONTROLLER", ""))
ap.add_argument("--period", type=float, default=0.1)
ap.add_argument("--service-ms", type=float, default=5.0)
args = ap.parse_args()

with open(os.path.join(args.data, "kv.json")) as f:
    KV = json.load(f)

est = Estimators(os.path.join(args.data, "b2_kv.json"), None)
pool = Pool(args.site, "kv", args.workers, lambda i: None, args.log,
            timeout=5.0, estimators=est)
mcp = FastMCP("kv-lookup", stateless_http=True, json_response=True,
              log_level="ERROR")
quiet_mcp_logging()


def _get(_, a):
    time.sleep(args.service_ms / 1000.0)
    k = a["key"].strip()
    if k not in KV:
        raise KeyError(f"no such key {k!r}")
    return {"key": k, "value": KV[k]}


@mcp.tool()
async def kv_get(key: str, ctx: Context) -> dict:
    """Get one record by key, e.g. 'customer:42' or 'supplier:7'."""
    return await pool.submit("kv_get", {"key": key}, _get, header(ctx, "x-call-id"),
                             is_local(ctx))


@mcp.custom_route("/status", methods=["GET"])
async def status(request: Request) -> JSONResponse:
    return JSONResponse(pool.status())


@mcp.custom_route("/ping", methods=["GET"])
async def ping(request: Request) -> JSONResponse:
    return JSONResponse({"site": args.site, "svc": "kv", "ts": time.time()})


async def main():
    await pool.start()
    asyncio.create_task(pool.pusher(args.controller, args.period))
    cfg = uvicorn.Config(mcp.streamable_http_app(), host="0.0.0.0",
                         port=args.port, log_level="warning",
                         access_log=False, timeout_keep_alive=5)
    await uvicorn.Server(cfg).serve()


if __name__ == "__main__":
    asyncio.run(main())
