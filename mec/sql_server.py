#!/usr/bin/env python3
"""MCP SQL analytics server: one replica at one MEC site.

Each site keeps its own read-only TPC-H SF1 copy. Every worker has its own
DuckDB instance (own file copy, threads=1), so one worker behaves like one
single-core server and run times do not depend on what the other worker does.
All three tools go through the same FIFO worker queue, like a real server
with a fixed number of workers.

Transport: MCP Streamable HTTP, stateless, JSON responses (spec 2026-07-28
has no session, so every call is one independent HTTP request).
"""
import argparse
import asyncio
import os
import re
import time

import duckdb
import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcpsite import Estimators, Pool, header, quiet_mcp_logging

ap = argparse.ArgumentParser()
ap.add_argument("--site", default=os.environ.get("SITE", "mec1"))
ap.add_argument("--data", default=os.environ.get("DATA", "/data"))
ap.add_argument("--workers", type=int, default=int(os.environ.get("WORKERS", "2")))
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--log", default="/var/log/mcp/sql_calls.jsonl")
ap.add_argument("--controller", default=os.environ.get("CONTROLLER", ""))
ap.add_argument("--period", type=float, default=0.1)
ap.add_argument("--timeout", type=float, default=30.0)
ap.add_argument("--max-rows", type=int, default=50)
args = ap.parse_args()

TABLES = ["region", "nation", "supplier", "customer", "part", "partsupp",
          "orders", "lineitem"]


def open_worker(i):
    path = os.path.join(args.data, f"tpch_w{i}.duckdb")
    return duckdb.connect(path, read_only=True,
                          config={"threads": 1, "memory_limit": "3GB",
                                  "temp_directory": f"/tmp/duckdb_w{i}"})


est = Estimators(os.path.join(args.data, "b2_sql.json"),
                 os.path.join(args.data, "oracle_sql.json"))
pool = Pool(args.site, "sql", args.workers, open_worker, args.log,
            timeout=args.timeout, estimators=est,
            on_timeout=lambda con: con.interrupt())


def load_model():
    """Method v1: run-time model trained offline (predict/train.py). EXPLAIN
    runs on its own small DuckDB instance (the base file), under a lock."""
    path = os.path.join(args.data, "runtime_model.pkl")
    if not os.path.exists(path):
        return None
    import pickle
    import threading

    import numpy as np
    import pandas as pd
    from features import call_features
    with open(path, "rb") as fh:
        mdl = pickle.load(fh)
    con = duckdb.connect(os.path.join(args.data, "tpch.duckdb"), read_only=True,
                         config={"threads": 1, "memory_limit": "512MB"})
    lock = threading.Lock()
    cols = mdl["columns"]

    def predict(tool, a):
        with lock:
            f = call_features(con, tool, a)
        x = pd.DataFrame([[f.get(c, 0.0) for c in cols]], columns=cols)
        return float(np.exp(mdl["mean"].predict(x)[0]) * mdl["smear"])
    return predict


est.model = load_model()
mcp = FastMCP("tpch-sql", stateless_http=True, json_response=True,
              log_level="ERROR")
quiet_mcp_logging()


def _clean(v):
    if isinstance(v, (int, float, str)) or v is None:
        return v
    return str(v)


def _list_tables(con, a):
    out = []
    for t in TABLES:
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        out.append({"table": t, "rows": n})
    return {"tables": out}


def _describe(con, a):
    t = a["table"].strip().lower()
    if t not in TABLES:
        raise ValueError(f"unknown table {t!r}; tables: {', '.join(TABLES)}")
    rows = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position", [t]).fetchall()
    return {"table": t, "columns": [{"name": r[0], "type": r[1]} for r in rows]}


_WRITE = re.compile(r"^\s*(insert|update|delete|create|drop|alter|attach|copy|"
                    r"install|load|pragma|set|export|import)\b", re.I)


def _run_sql(con, a):
    q = a["query"].strip().rstrip(";").strip()
    if _WRITE.match(q):
        raise ValueError("read-only server: only SELECT / WITH queries")
    # The server caps the result at max_rows, as most SQL tools do.
    cur = con.execute(f"SELECT * FROM ({q}) AS _q LIMIT {args.max_rows + 1}")
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    trunc = len(rows) > args.max_rows
    rows = [[_clean(v) for v in r] for r in rows[: args.max_rows]]
    return {"columns": cols, "rows": rows, "truncated": trunc}


@mcp.tool()
async def list_tables(ctx: Context) -> dict:
    """List the TPC-H tables and their row counts."""
    return await pool.submit("list_tables", {}, _list_tables, header(ctx, "x-call-id"))


@mcp.tool()
async def describe_table(table: str, ctx: Context) -> dict:
    """Show the columns and types of one TPC-H table."""
    return await pool.submit("describe_table", {"table": table}, _describe,
                             header(ctx, "x-call-id"))


@mcp.tool()
async def run_sql(query: str, ctx: Context) -> dict:
    """Run one read-only SQL query (DuckDB dialect) on the TPC-H database.
    Returns at most 50 rows."""
    return await pool.submit("run_sql", {"query": query}, _run_sql,
                             header(ctx, "x-call-id"))


@mcp.custom_route("/status", methods=["GET"])
async def status(request: Request) -> JSONResponse:
    return JSONResponse(pool.status())


@mcp.custom_route("/ping", methods=["GET"])
async def ping(request: Request) -> JSONResponse:
    return JSONResponse({"site": args.site, "svc": "sql", "ts": time.time()})


async def main():
    await pool.start()
    asyncio.create_task(pool.pusher(args.controller, args.period))
    app = mcp.streamable_http_app()
    cfg = uvicorn.Config(app, host="0.0.0.0", port=args.port,
                         log_level="warning", access_log=False,
                         timeout_keep_alive=5)
    await uvicorn.Server(cfg).serve()


if __name__ == "__main__":
    asyncio.run(main())
