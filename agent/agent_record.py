#!/usr/bin/env python3
"""Run the Qwen3-8B agent on the 50 tasks and record every MCP call chain.

The agent talks to an OpenAI-compatible LLM server (vLLM, tool calling on)
and calls the MCP tools over Streamable HTTP, one new connection per call,
on one unloaded replica (so the run time the replica logs is the run time
in isolation). Output, one JSON line per task run:
  task, template, run, steps[{tool, args, call_id, think_s, client_s, ok}],
  answer, n_calls, llm_s, wall_s
The replica's own log (call_id -> wait_s, run_s) is joined later.
"""
import argparse
import json
import os
import socket
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from openai import OpenAI

sys.path.insert(0, os.path.dirname(__file__))
from tasks import all_tasks  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--llm", default="http://127.0.0.1:8000/v1")
ap.add_argument("--model", default="qwen3-8b")
ap.add_argument("--key", default=os.environ.get("LLM_KEY", "none"))
ap.add_argument("--sql", default="http://10.201.1.10:8000/mcp")
ap.add_argument("--kv", default="http://10.201.1.10:8001/mcp")
ap.add_argument("--src", default=None, help="bind source IP (UE tunnel)")
ap.add_argument("--runs", type=int, default=5)
ap.add_argument("--max-steps", type=int, default=15)
ap.add_argument("--parallel", type=int, default=8, help="task runs at once")
ap.add_argument("--temperature", type=float, default=0.7)
ap.add_argument("--think", action="store_true", help="enable Qwen3 thinking")
ap.add_argument("--out", default="results/chains.jsonl")
ap.add_argument("--only", default="", help="comma list of task ids")
a = ap.parse_args()

SYSTEM = ("You are a data analyst agent. You answer questions about a TPC-H database "
          "(tables region, nation, supplier, customer, part, partsupp, orders, lineitem; "
          "DuckDB SQL dialect) by calling tools. Column names carry the table prefix: "
          "r_ (region), n_ (nation), s_ (supplier), c_ (customer), p_ (part), ps_ (partsupp), "
          "o_ (orders), l_ (lineitem), e.g. l_extendedprice, o_orderdate, s_suppkey. "
          "If you are not sure about a column, call describe_table first. "
          "If a query fails, read the error and fix the query. "
          "Use kv_get for single customer or supplier records (keys 'customer:<custkey>', "
          "'supplier:<suppkey>'). Use one tool call at a time. When you have the answer, "
          "reply with a short final answer and no tool call.")

TOOLS = [
    {"type": "function", "function": {
        "name": "list_tables", "description": "List the TPC-H tables and their row counts.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "describe_table", "description": "Show the columns and types of one table.",
        "parameters": {"type": "object", "properties": {"table": {"type": "string"}},
                       "required": ["table"]}}},
    {"type": "function", "function": {
        "name": "run_sql",
        "description": "Run one read-only SQL query (DuckDB). Returns at most 50 rows.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "kv_get", "description": "Get one customer or supplier record by key.",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}},
                       "required": ["key"]}}},
]
SQL_URLS = a.sql.split(",")                  # several replicas: one per task run, round robin
KV_URLS = a.kv.split(",")


def servers(i):
    s, k = SQL_URLS[i % len(SQL_URLS)], KV_URLS[i % len(KV_URLS)]
    return {"list_tables": s, "describe_table": s, "run_sql": s, "kv_get": k}


SERVER = servers(0)
HDR = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
       "Connection": "close"}
llm = OpenAI(base_url=a.llm, api_key=a.key, timeout=300)


def mcp_call(tool, args, call_id, srv):
    tr = httpx.HTTPTransport(local_address=a.src, socket_options=[
        (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]) if a.src else httpx.HTTPTransport(
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)])
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}
    with httpx.Client(transport=tr, headers=HDR | {"X-Call-Id": call_id}, timeout=60) as c:
        r = c.post(srv[tool], json=body)
    r.raise_for_status()
    res = r.json()
    if "error" in res:
        return False, json.dumps(res["error"])[:2000]
    content = res["result"].get("content", [])
    text = content[0].get("text", "") if content else ""
    return not res["result"].get("isError", False), text[:4000]


def run_one(task, run, idx=0):
    srv = servers(idx)
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task["prompt"]}]
    steps, llm_s, t0 = [], 0.0, time.time()
    answer = None
    for _ in range(a.max_steps):
        t_l = time.time()
        resp = llm.chat.completions.create(
            model=a.model, messages=msgs, tools=TOOLS, tool_choice="auto",
            temperature=a.temperature, max_tokens=1024,
            extra_body={"chat_template_kwargs": {"enable_thinking": a.think}})
        think = time.time() - t_l
        llm_s += think
        m = resp.choices[0].message
        if not m.tool_calls:
            answer = m.content
            break
        tc = m.tool_calls[0]                 # one call at a time (sequential chain)
        msgs.append({"role": "assistant", "content": m.content or "",
                     "tool_calls": [{"id": tc.id, "type": "function", "function": {
                         "name": tc.function.name, "arguments": tc.function.arguments}}]})
        try:
            args = json.loads(tc.function.arguments or "{}")
        except ValueError:
            args = {}
        call_id = uuid.uuid4().hex[:16]
        t_c = time.time()
        try:
            ok, out = mcp_call(tc.function.name, args, call_id, srv) if tc.function.name in srv \
                else (False, f"unknown tool {tc.function.name}")
        except Exception as e:  # noqa: BLE001
            ok, out = False, f"{type(e).__name__}: {e}"[:500]
        steps.append({"tool": tc.function.name, "args": args, "call_id": call_id,
                      "think_s": think, "client_s": time.time() - t_c, "ok": ok})
        msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out})
    return {"task": task["task"], "template": task["template"], "run": run,
            "steps": steps, "n_calls": len(steps), "answer": answer,
            "sql_url": srv["run_sql"], "llm_s": llm_s, "wall_s": time.time() - t0}


def main():
    tasks = all_tasks()
    if a.only:
        keep = set(a.only.split(","))
        tasks = [t for t in tasks if t["task"] in keep]
    jobs = [(t, r) for r in range(a.runs) for t in tasks]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    done = set()
    if os.path.exists(a.out):
        for line in open(a.out):
            d = json.loads(line)
            done.add((d["task"], d["run"]))
    jobs = [j for j in jobs if (j[0]["task"], j[1]) not in done]
    print(f"{len(jobs)} task runs to do", flush=True)
    n_calls = 0
    with open(a.out, "a", buffering=1) as f, ThreadPoolExecutor(a.parallel) as ex:
        for i, rec in enumerate(ex.map(lambda ij: run_one(ij[1][0], ij[1][1], ij[0]),
                                       enumerate(jobs))):
            f.write(json.dumps(rec) + "\n")
            n_calls += rec["n_calls"]
            if i % 10 == 0:
                print(f"{i + 1}/{len(jobs)} runs, {n_calls} calls", flush=True)
    print(f"done: {n_calls} calls", flush=True)


if __name__ == "__main__":
    main()
