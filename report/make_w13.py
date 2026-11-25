#!/usr/bin/env python3
"""W13 check page (HTML -> PDF): method v1, 10 runs, connection setup cost,
continue / pivot verdict. Numbers come from results/runs/<tag>_stats.json
(harness/w13_stats.py) and results/conn_cost.csv.
Usage: make_w13.py <tag> <out.html>"""
import csv
import json
import os
import sys

tag, out = sys.argv[1], sys.argv[2]
R = os.path.join(os.path.dirname(__file__), "..", "results")
S = json.load(open(os.path.join(R, "runs", f"{tag}_stats.json")))
cc = list(csv.DictReader(open(os.path.join(R, "conn_cost.csv"))))
sm, pr = S["summary"], S["paired_vs_b1"]
NAMES = {"b0": "B0 nearest", "b1": "B1 fewest in-flight (CATS-style)",
         "b2": "B2 per-tool mean wait", "method": "Wait-aware method v1",
         "oracle": "Oracle wait (true run times)"}
ORDER = [p for p in ["b0", "b1", "b2", "method", "oracle"] if p in sm]


def pc(x):
    return f"{100 * x:.1f}%"


def gain_cell(pol, m):
    if pol not in pr:
        return "—"
    g = pr[pol][m]
    return f"{pc(g['mean'])} [{pc(g['ci'][0])}, {pc(g['ci'][1])}]"


rows = "".join(
    f"<tr><td>{NAMES[p]}</td><td>{sm[p]['seeds']}</td><td>{sm[p]['p50'] * 1000:.0f}</td>"
    f"<td>{sm[p]['p95'] * 1000:.0f}</td><td>{sm[p]['short_p95'] * 1000:.0f}</td>"
    f"<td>{sm[p]['long_p95'] * 1000:.0f}</td><td>{gain_cell(p, 'p95')}</td>"
    f"<td>{gain_cell(p, 'short_p95')}</td>"
    f"<td>{100 * sm[p]['sites']['mec1']:.0f} / {100 * sm[p]['sites']['mec2']:.0f} / {100 * sm[p]['sites']['mec3']:.0f}</td></tr>"
    for p in ORDER)
cc_rows = "".join(
    f"<tr><td>{['MEC-1', 'MEC-2', 'MEC-3'][i]}</td><td>{float(r['new_p50_ms']):.1f}</td>"
    f"<td>{float(r['kept_p50_ms']):.1f}</td><td>{float(r['setup_cost_p50_ms']):.1f}</td></tr>"
    for i, r in enumerate(cc))

g = pr["method"]["p95"]
gs = pr["method"]["short_p95"]
go = pr.get("oracle", {}).get("p95")
mean = g["mean"]
if mean < 0.05:
    verdict = ("Below the 5% line: by the rule fixed in the proposal, I pivot to plan B, the "
               "measurement paper")
elif mean < 0.20:
    verdict = ("Between 5% and 20%: by the rule fixed in the proposal, I continue and use the "
               "measured number as the claim")
else:
    verdict = "At least 20%: the claim of the proposal holds; I continue"
floor_b1 = sm["b1"]["floor_p95"] * 1000

html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>W13 Check - 1102549018 Bui Duong Kham</title>
<style>
  @page {{ size: A4; margin: 16mm; }}
  body {{ font-family: "Times New Roman", Times, serif; font-size: 10.6pt; line-height: 1.28; color: #000; margin: 0; }}
  .title {{ text-align: center; margin-bottom: 6px; }} .title h1 {{ font-size: 13.5pt; margin: 3px 0 4px; }}
  hr {{ border: 0; border-top: 1px solid #000; margin: 5px 0 4px; }}
  h2 {{ font-size: 11.2pt; margin: 9px 0 3px; }}
  p {{ margin: 3px 0 5px; text-align: justify; }} ul {{ margin: 2px 0 4px; padding-left: 20px; }}
  li {{ margin: 1px 0; text-align: justify; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 9.4pt; margin: 3px 0 3px; }}
  th, td {{ border: 1px solid #777; padding: 2px 4px; vertical-align: top; text-align: left; }}
  th {{ background: #eee; }} .cap {{ font-size: 9.2pt; margin: 1px 0 6px; }}
</style></head><body>
<div class="title"><div>Machine Learning and AI, Fall 2026 – W13 Check</div>
<h1>Method v1 and the give-up check</h1>
<div>Bui Duong Kham (1102549018) &nbsp;|&nbsp; 2 December 2026</div></div><hr>

<h2>1. Verdict</h2>
<p>In the main setting, the wait-aware method lowers p95 call latency by {pc(mean)} compared with B1
(mean of 10 paired runs, 95% CI {pc(g['ci'][0])} to {pc(g['ci'][1])}; better than B1 in {g['wins']} of 10 runs).
{verdict}. For short calls, the gain is {pc(gs['mean'])} (CI {pc(gs['ci'][0])} to {pc(gs['ci'][1])}).
{'With exact run times (oracle) the gain is ' + pc(go['mean']) + ', so better prediction alone would add little; the limit is the setting, not the model.' if go else ''}</p>

<h2>2. Method v1 as run</h2>
<ul>
  <li>On arrival, each replica predicts the run time of the call from its arguments (Section 4 of the W12 package) and scales the prediction by an online calibration factor (moving average of measured / predicted run time), like the re-synchronisation of the W7 twin.</li>
  <li>The reported wait is the time until a worker is free for a new call, found by replaying the queue on the two workers. My first version used (remaining work) / (workers); with two workers this is wrong, because one long call next to an idle worker means no wait. With that version even the oracle lost to B1.</li>
  <li>The controller does not send all calls of a 100 ms interval to one replica. It splits the calls expected in the next 200 ms over the replicas by water-filling, so that one stale view is not followed by a herd of calls to one site. B1 and B2 use the same split with their own signal (call count, per-tool mean), so all policies get the same tuning.</li>
  <li>Setting (as in W12): 30 UEs with three agents each, 20 test tasks, offered work 70% of the measured capacity, local users using 30% of MEC-1, 300 s per run (first 60 s dropped), 10 seeds; the same seed offers the same calls to every policy.</li>
</ul>

<h2>3. Results</h2>
<table><tr><th>Policy</th><th>Runs</th><th>p50, ms</th><th>p95, ms</th><th>p95 short, ms</th><th>p95 long, ms</th><th>p95 gain vs B1 [95% CI]</th><th>Short-call p95 gain vs B1</th><th>MEC-1 / 2 / 3, %</th></tr>{rows}</table>
<div class="cap">Table 1. Means over runs. Gains are paired by seed (1 − policy / B1) with a bootstrap CI over seeds. Short: run time &lt; 50 ms when recorded.</div>
<p>Without any queue wait, B1's p95 would be {floor_b1:.0f} ms (latency minus queue wait), so most of the p95 is waiting, but all three sites are busy at this load and moving a call only helps when another site has a free worker soon. The 4 December sweep varies exactly this: load from 30% to 90% and the share of MEC-1 used by local users.</p>

<h2>4. Connection setup cost</h2>
<table><tr><th>Site</th><th>One call, new connection, p50 ms</th><th>One call, kept-alive connection, p50 ms</th><th>Setup cost, ms</th></tr>{cc_rows}</table>
<div class="cap">Table 2. 200 kv_get calls per cell from one UE through the UPF. Steering per call needs a new connection per call, so every call pays one extra round trip.</div>
<p>The setup cost is one round trip (about 0.3, 10 and 31 ms). It is small next to the waits above (hundreds of ms to seconds) but not next to a 5 ms lookup: for short calls at low load, per-call steering to a far site costs more than it saves. This is part of the crossover for 4 December.</p>

<h2>5. Next (4 December)</h2>
<p>B2 and the oracle are already in Table 1. For class: the load (30–90%) × skew sweep with B1, B2 and the method, the crossover marked, and the failure cases.</p>
</body></html>
"""
open(out, "w", encoding="utf-8").write(html)
print("wrote", out)
