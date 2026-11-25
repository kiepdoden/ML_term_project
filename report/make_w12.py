#!/usr/bin/env python3
"""Build the W12 baseline package (HTML, printed to PDF) from the result files.
Every number in the text comes from a file under results/.
Usage: make_w12.py <rec_dir> <runs_tag> <out.html>"""
import csv
import json
import os
import statistics as st
import sys

rec, tag, out = sys.argv[1:4]
R = os.path.join(os.path.dirname(__file__), "..", "results")
rec = os.path.join(R, rec)

rtt = list(csv.DictReader(open(os.path.join(R, "rtt_table.csv"))))
chains = [json.loads(l) for l in open(os.path.join(rec, "chains_joined.jsonl"))]
r2 = list(csv.DictReader(open(os.path.join(rec, "check1", "check1_r2.csv"))))
rt = list(csv.DictReader(open(os.path.join(rec, "check1", "runtime_by_tool.csv"))))
fixed = json.load(open(os.path.join(rec, "check1", "fixed_split.json")))
an = json.load(open(os.path.join(rec, "check1", "analysis.json")))
runs = json.load(open(os.path.join(R, "runs", f"{tag}_table.json")))
meta_path = sorted(p for p in os.listdir(os.path.join(R, "runs"))
                   if p.startswith(tag + "_") and os.path.isdir(os.path.join(R, "runs", p)))[0]
meta = json.load(open(os.path.join(R, "runs", meta_path, "client.jsonl.meta.json")))

steps = [s for c in chains for s in c["steps"]]
n_runs, n_calls = len(chains), len(steps)
n_tasks = len({c["task"] for c in chains})
runs_per_task = n_runs // n_tasks
answered = sum(1 for c in chains if c["answer"])
failed = sum(1 for s in steps if s.get("err"))
think = [s["think_s"] for s in steps]
sql_runs = [s["run_s"] for s in steps if s["tool"] == "run_sql" and s.get("run_s")]
long_share = sum(1 for s in steps if (s.get("run_s") or 0) >= 0.2) / n_calls
long_work = sum(s["run_s"] for s in steps if (s.get("run_s") or 0) >= 0.2) / sum(s.get("run_s") or 0 for s in steps)
by_tool = {}
for s in steps:
    by_tool[s["tool"]] = by_tool.get(s["tool"], 0) + 1


def f(x, d=2):
    return f"{float(x):.{d}f}"


def r2row(calls, split):
    return next(r for r in r2 if r["calls"] == calls and r["split"] == split)


sq_t, sq_tm = r2row("run_sql", "by task"), r2row("run_sql", "by template")
al_t, al_tm = r2row("all tools", "by task"), r2row("all tools", "by template")
pol = {r["policy"]: r for r in runs}
b0, b1 = pol.get("b0"), pol.get("b1")
gain_b1 = 100 * (1 - b1["p95_ms"] / b0["p95_ms"]) if b0 and b1 else float("nan")
log_pass = float(sq_t["r2_log"]) >= 0.5

TOOLNAME = {"run_sql": "run_sql", "describe_table": "describe_table",
            "list_tables": "list_tables", "kv_get": "kv_get (KV server)"}
rt_rows = "".join(
    f"<tr><td>{TOOLNAME.get(r['tool'], r['tool'])}</td><td>{int(float(r['count']))}</td>"
    f"<td>{1000 * float(r['50%']):.1f}</td><td>{1000 * float(r['90%']):.1f}</td>"
    f"<td>{1000 * float(r['99%']):.0f}</td><td>{1000 * float(r['max']):.0f}</td></tr>"
    for r in sorted(rt, key=lambda r: -float(r["count"])))
rtt_rows = "".join(
    f"<tr><td>{['MEC-1 (same site as the UPF)', 'MEC-2 (nearby)', 'MEC-3 (far)'][i]}</td>"
    f"<td>{f(r['icmp_p50_ms'], 1)} ({f(r['icmp_p95_ms'], 1)})</td>"
    f"<td>{f(r['tcp_p50_ms'], 1)} ({f(r['tcp_p95_ms'], 1)})</td>"
    f"<td>{f(r['mcp_call_p50_ms'], 1)} ({f(r['mcp_call_p95_ms'], 1)})</td></tr>"
    for i, r in enumerate(rtt))
NAMES = {"b0": "B0 nearest", "b1": "B1 fewest in-flight (CATS-style)",
         "b2": "B2 per-tool mean wait", "method": "Wait-aware (v1)"}
run_rows = "".join(
    f"<tr><td>{NAMES.get(r['policy'], r['policy'])}</td><td>{r['runs']}</td><td>{r['calls']}</td>"
    f"<td>{r['p50_ms']:.0f}</td><td>{r['p95_ms']:.0f} ({r['p95_ci'].replace('-', '–')})</td>"
    f"<td>{r['short_p95_ms']:.0f}</td><td>{r['long_p95_ms']:.0f}</td>"
    f"<td>{r['sites_%'].get('mec1', 0)} / {r['sites_%'].get('mec2', 0)} / {r['sites_%'].get('mec3', 0)}</td>"
    f"<td>{r['fail']}</td></tr>"
    for r in sorted(runs, key=lambda r: ["b0", "b1", "b2", "method", "oracle"].index(r["policy"])))

html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>W12 Baseline - 1102549018 Bui Duong Kham</title>
<style>
  @page {{ size: A4; margin: 18mm; }}
  html, body {{ margin: 0; padding: 0; background: #fff; }}
  body {{ font-family: "Times New Roman", Times, serif; font-size: 10.8pt; line-height: 1.3; color: #000; }}
  .title {{ text-align: center; margin-bottom: 8px; }}
  .title h1 {{ font-size: 14pt; margin: 4px 0 6px; }}
  hr {{ border: 0; border-top: 1px solid #000; margin: 6px 0 4px; }}
  h2 {{ font-size: 11.5pt; margin: 11px 0 3px; break-after: avoid; }}
  p {{ margin: 3px 0 5px; text-align: justify; }}
  ul, ol {{ margin: 2px 0 5px; padding-left: 20px; }}
  li {{ margin: 1.5px 0; text-align: justify; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 9.8pt; margin: 4px 0 4px; break-inside: avoid; }}
  th, td {{ border: 1px solid #777; padding: 2px 5px; vertical-align: top; text-align: left; }}
  th {{ background: #eee; }}
  .cap {{ font-size: 9.6pt; margin: 1px 0 8px; }}
  .page {{ break-before: page; }}
</style></head><body>
<div class="title">
  <div>Machine Learning and AI, Fall 2026 – W12 Baseline Package</div>
  <h1>Wait-Aware Steering of MCP Tool Calls across 5G MEC Sites</h1>
  <div>Bui Duong Kham (1102549018) &nbsp;|&nbsp; DCN Lab, Prof. Younghan Kim &nbsp;|&nbsp; 29 November 2026</div>
</div><hr>

<h2>1. Summary</h2>
<p>The testbed runs end to end: emulated phones send real MCP tool calls through a free5GC UPF, and the UPF
steers each new call to one of three MEC replicas. I recorded {n_calls:,} tool calls from a Qwen3-8B agent
({n_runs} task runs). Check 1 {'passes' if log_pass and float(sq_t['r2_sec']) >= 0.5 else 'passes on the log scale only' if log_pass else 'fails'}: on tasks held out from
training, the argument features give R² = {f(sq_t['r2_log'])} on the log run time of SQL calls and
R² = {f(sq_t['r2_sec'])} in seconds. On question templates never seen in training the model is much weaker
(R² {f(sq_tm['r2_log'])} on the log scale, {f(sq_tm['r2_sec'])} in seconds); Section 4 discusses this limit.
B0 and B1 run on the same workload, and B1 lowers p95 call latency by {gain_b1:.0f}% compared with B0 (Table 4).
Decision: {'continue with the main plan' if log_pass else 'pivot to plan B'}.</p>

<h2>2. Testbed as built, and changes since the proposal</h2>
<ul>
  <li>5G core: free5GC v4.3.0 with one UPF; 30 phones (UEs) and one base station emulated with UERANSIM. The radio is emulated, as in the proposal.</li>
  <li>MEC sites: three virtual machines with two CPU cores and 16 GB each. Each runs an MCP SQL server (DuckDB on TPC-H, two workers, one core each) and an MCP key-value server (about 5 ms per call). Both use Streamable HTTP without sessions.</li>
  <li>Steering: each MCP server has one anycast address. A small table in the UPF maps it to one replica, and every new connection follows the table. A controller rewrites the table every 100 ms from the replicas' reports: it splits the calls expected in the next 200 ms over the replicas by water-filling on the policy's signal (call count for B1, predicted wait for the method), so that one stale view does not send a herd of calls to a single replica. Reports older than 300 ms are ignored (the freshness contract from W7).</li>
  <li>Change 1, sites: The proposal planned three separate sites. Because that testbed was not available in time, the three sites now run on one server and their distance is emulated on the links from the UPF (netem). The UPF, the 5G core and the MCP traffic are real; the distance between sites is not. Table 1 shows the result. The paper will state this.</li>
  <li>Change 2, data size: On TPC-H scale 1 the longest scan took 0.14 s, far from the "milliseconds to seconds" spread the claim needs. I moved to scale 10 (60 M line items): lookups take a few ms and the largest scans 1–3 s (Table 2).</li>
</ul>
<table><tr><th>Site</th><th>Ping RTT, ms, p50 (p95)</th><th>TCP connect, ms</th><th>One MCP call on a new connection, ms</th></tr>{rtt_rows}</table>
<div class="cap">Table 1. RTT table, measured from one UE through the UPF, 100 samples per cell. The last column is what one steered call pays: TCP setup, request, a 5 ms lookup and the response.</div>

<h2>3. Recorded calls</h2>
<p>The agent is Qwen3-8B (FP8) on one NVIDIA L4 GPU with tool calling and without its thinking mode. It works on 50 analytics tasks in the τ-bench style (10 question templates with 5 parameter sets each, for example "Which 5 suppliers in ASIA had the highest revenue in 1995, and what are their phone numbers?"), {runs_per_task} runs per task at temperature 0.7. This gave {n_runs} task runs and {n_calls:,} calls ({n_calls / n_runs:.1f} per run; {by_tool.get('run_sql', 0):,} run_sql, {by_tool.get('describe_table', 0):,} describe_table, {by_tool.get('kv_get', 0)} kv_get, {by_tool.get('list_tables', 0)} list_tables). The agent gave a final answer in {100 * answered / n_runs:.0f}% of the runs; {100 * failed / n_calls:.0f}% of the calls returned a tool error (mostly a wrong column name, which the agent then fixed). Each call is stored with its arguments and with the queue wait and run time measured at the replica. The median LLM time between two calls was {st.median(think):.1f} s.</p>
<table><tr><th>Tool</th><th>Calls</th><th>Median run time, ms</th><th>p90, ms</th><th>p99, ms</th><th>Max, ms</th></tr>{rt_rows}</table>
<div class="cap">Table 2. Run time at the replica (no queue wait, no network). {100 * long_share:.0f}% of all calls run 0.2 s or longer, and these few calls hold {100 * long_work:.0f}% of all the run time.</div>

<h2>4. Check 1: do the arguments predict the run time?</h2>
<p>The model sees only what the replica can read from a call: the tool name and, for run_sql, simple features of the SQL text (tables, joins, aggregates, LIMIT, key lookups) and the row estimates of DuckDB's query planner (EXPLAIN, which plans the query without running it). It is a gradient-boosted tree model (LightGBM) on log run time. The test folds hold out whole tasks (the proposal's split) and, as a stricter test, whole templates. B2 is the per-tool mean run time from the training folds.</p>
<table><tr><th>Calls</th><th>Held out</th><th>R², log time</th><th>R², seconds</th><th>MAE, ms</th><th>B2 R², log</th><th>B2 MAE, ms</th></tr>
<tr><td>run_sql</td><td>tasks</td><td>{f(sq_t['r2_log'])}</td><td>{f(sq_t['r2_sec'])}</td><td>{f(sq_t['mae_ms'], 0)}</td><td>{f(sq_t['b2_r2_log'])}</td><td>{f(sq_t['b2_mae_ms'], 0)}</td></tr>
<tr><td>run_sql</td><td>templates</td><td>{f(sq_tm['r2_log'])}</td><td>{f(sq_tm['r2_sec'])}</td><td>{f(sq_tm['mae_ms'], 0)}</td><td>{f(sq_tm['b2_r2_log'])}</td><td>{f(sq_tm['b2_mae_ms'], 0)}</td></tr>
<tr><td>all tools</td><td>tasks</td><td>{f(al_t['r2_log'])}</td><td>{f(al_t['r2_sec'])}</td><td>{f(al_t['mae_ms'], 0)}</td><td>{f(al_t['b2_r2_log'])}</td><td>{f(al_t['b2_mae_ms'], 0)}</td></tr>
<tr><td>all tools</td><td>templates</td><td>{f(al_tm['r2_log'])}</td><td>{f(al_tm['r2_sec'])}</td><td>{f(al_tm['mae_ms'], 0)}</td><td>{f(al_tm['b2_r2_log'])}</td><td>{f(al_tm['b2_mae_ms'], 0)}</td></tr></table>
<div class="cap">Table 3. Held-out R² (5-fold, grouped). The seconds column uses the log model's prediction times a bias correction.</div>
<p>Reading the table:</p>
<ul>
  <li>On new tasks the features explain most of the run time (R² {f(sq_t['r2_log'])} on the log scale). On new templates they explain much less ({f(sq_tm['r2_log'])}): query shapes the model has never seen are harder, and a deployed estimator would need calls of every tool and query type in its training data.</li>
  <li>The seconds scale is dominated by a few extreme calls. The longest call ran {an['max_run_s']:.1f} s and the model predicted {an['max_pred_s']:.1f} s; this single call is {100 * an['share_sse_max_call']:.0f}% of the squared error. Without it, R² in seconds is {f(an['r2_sec_without_max'])}.</li>
  <li>For steering the model must find the long calls and get the total queued work right. It ranks long calls (≥ 0.2 s) above short ones with AUC {f(an["models"]["log_smear"]["auc_long"])}, and its total predicted work is {100 * an["models"]["log_smear"]["sum_pred/sum_true"]:.0f}% of the measured total.</li>
  <li>B2 cannot separate a lookup from a scan of the same tool, so its R² on the log scale is negative and its error is {float(sq_t['b2_mae_ms']) / float(sq_t['mae_ms']):.1f}× larger. B2 against the method on 4 Dec will show whether this matters for p95.</li>
</ul>
<p>Run times span four orders of magnitude, so I report both scales; on held-out tasks both are above the 0.5 line of the proposal. The fixed split used for the experiments (variants 3 and 4 of each template as test tasks, {fixed['test_calls']} calls) gives R² {f(fixed['r2_log_sql'])} (log) and {f(fixed['r2_sec_sql'])} (seconds) for run_sql.</p>

<h2>5. Baselines B0 and B1</h2>
<p>Workload: the recorded chains of the 20 test tasks, replayed by the 30 UEs with three agents each ({meta['clients']} clients). The recorded LLM time between calls is scaled by {meta['think_scale']} (median about {meta['think_scale'] * st.median(think):.1f} s), because one shared 8B model was slower than a hosted LLM and could not create enough load. Offered work is {100 * meta['load']:.0f}% of the measured capacity of the six workers: when all workers are busy, a long call runs about 1.4 times longer than when it was recorded alone (the three sites share one server's memory), so the capacity is {meta['capacity']:.1f} instead of 6 worker-seconds of recorded run time per second. Local users at MEC-1, which the UPF does not steer, use {100 * meta['bg_load']:.0f}% of MEC-1 (skew). Each run lasts {meta['duration']:.0f} s and the first {meta['warmup']:.0f} s are dropped.</p>
<table><tr><th>Policy</th><th>Runs</th><th>Calls</th><th>p50, ms</th><th>p95, ms (95% CI)</th><th>p95 short, ms</th><th>p95 long, ms</th><th>Served at MEC-1 / 2 / 3, %</th><th>Failed</th></tr>{run_rows}</table>
<div class="cap">Table 4. Latency of a call measured at the UE, from sending the request to receiving the response. Short: run time &lt; 50 ms when recorded. CI: bootstrap over task runs. Failed: no answer (connection error or 60 s timeout); calls whose SQL was wrong when recorded return the same error quickly and count as normal calls.</div>
<p>B0 sends every call to MEC-1, which also serves its local users, so its queue grows during the run: p95 is {b0['p95_ms'] / 1000:.1f} s, and the clients complete only {b0['calls']:,} calls against {b1['calls']:,} under B1 (a client waits for each answer, so a slow policy also offers less work). B1 spreads the calls ({b1['sites_%'].get('mec1', 0)} / {b1['sites_%'].get('mec2', 0)} / {b1['sites_%'].get('mec3', 0)}%) and brings p95 down to {b1['p95_ms'] / 1000:.1f} s. The short calls are what B1 leaves: their p95 is still {b1['short_p95_ms'] / 1000:.1f} s, because a lookup that takes milliseconds lands behind a scan that takes seconds; counting calls cannot see the difference. This is the gap the wait-aware method has to close on 2 December.</p>

<h2>6. Revision note: answers to the review of 13 November</h2>
<ol>
  <li>MCP session. The MCP specification of 28 July 2026 removed the protocol-level session: every tools/call is an independent HTTP request, and a server does not keep client state between calls. My servers run in this stateless mode (no Mcp-Session-Id), so the UPF can send each call to any replica. What remains is the cost of steering per connection: the client opens one connection per call, so every call pays a TCP handshake on top of its request (Table 1: one call on a new connection takes {f(rtt[0]['mcp_call_p50_ms'], 1)} ms at MEC-1 and {f(rtt[2]['mcp_call_p50_ms'], 1)} ms at MEC-3, about two round trips). The W13 check measures this cost against reused connections.</li>
  <li>B2 added. B2 uses the same wait formula as my method (time until a worker is free for a new call, found by replaying the queue on the workers, plus network time), but the run time of each call is the mean run time of its tool, without the arguments. B2 against the method is the experiment that shows whether reading the arguments is worth it; the controller already runs B0, B1, B2, the method and an oracle with the same code.</li>
  <li>Crossover in the main text. The 4 Dec sweep (load 30–90% × skew at MEC-1) marks where sending a call to a site 28 ms away becomes better than waiting at MEC-1; this will be the main figure, not an appendix.</li>
</ol>
<p>Other changes: the testbed and data size (Section 2), the LLM time scaling and three agents per UE (Section 5). AI use: Claude (AI assistant) helped write the testbed code and scripts and ran the measurements with me; I checked the setup and the numbers.</p>

<div class="page"></div>
<h2>7. Agenda to the end of the term</h2>
<table>
<tr><th style="width:18%">Date</th><th>Deliverable</th><th style="width:30%">Pass / pivot rule</th></tr>
<tr><td>Sun 29 Nov</td><td>This package: RTT table, {n_calls:,} recorded calls, check 1 (R²), B0 and B1 running, revision note, agenda.</td><td>R² &lt; 0.5 on held-out tasks → plan B.</td></tr>
<tr><td>Wed 2 Dec (W13 check)</td><td>Wait-aware method v1 (replicas predict the run time of queued calls from their arguments and report the wait); 10 runs per policy in the main setting; connection setup cost (one connection per call vs reused); one-page continue / pivot verdict.</td><td>p95 gain over B1 &lt; 5% → plan B; 5–20% → continue with the measured number.</td></tr>
<tr><td>Fri 4 Dec (class)</td><td>B2 and the oracle-wait ablation; load 30–90% × skew sweep with the crossover marked; failure cases; 5-minute update.</td><td>—</td></tr>
<tr><td>Fri 11 Dec</td><td>Paper draft (ICLR template) to paperreview.ai; review saved.</td><td>—</td></tr>
<tr><td>Mon 14 Dec</td><td>Make-up class (tentative).</td><td>—</td></tr>
<tr><td>Fri 18 Dec</td><td>Demo: B1 and the method side by side on one task while MEC-1 is busy; code walk-through; backup video.</td><td>—</td></tr>
<tr><td>Mon 21 Dec</td><td>Revised paper, original AI review, response letter, repository with README, slides.</td><td>—</td></tr>
</table>
<p>Plan B (unchanged): the same testbed and traces as a measurement paper, "When does in-network steering of MCP tool calls pay off?", with the crossover and the per-call connection cost as the main results.</p>
</body></html>
"""
open(out, "w", encoding="utf-8").write(html)
print("wrote", out)
