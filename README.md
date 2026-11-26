# mcp_steer: wait-aware steering of MCP tool calls across 5G MEC sites

Term project, Machine Learning and AI (Soongsil University GSICT, Fall 2026).

An LLM agent on a 5G UE calls MCP tools. Each tool has a replica at three MEC sites, and
the UPF picks one replica for every new connection (one call = one connection). Today's
choices are the nearest site (B0) or the site with the fewest calls in flight (B1,
CATS-style). Neither sees how long the calls already queued at a site will run: a 5 ms
lookup can land behind a SQL scan of several seconds.

**Method.** On arrival, each replica predicts the run time of every call from its
arguments (LightGBM on features from the SQL text and `EXPLAIN`, with an online
calibration factor), replays its FIFO queue over its workers, and reports the time until
a worker is free for a new call. Every 100 ms the controller splits the new connections of
the next 200 ms over the sites by water-filling on (reported wait + RTT). A report older
than 300 ms is not used.

## Main result (W13 check)

30 UEs × 3 agents replaying recorded call chains, offered work 70% of capacity, local
users taking 30% of MEC-1, 300 s per run (first 60 s dropped). Gains are paired by seed
(1 − policy / B1), 95% bootstrap CI over seeds.

| Policy | Runs | p50 ms | p95 ms | p95 short calls ms | p95 gain vs B1 [95% CI] |
|---|---|---|---|---|---|
| B0 nearest | 3 | 2974 | 10158 | 9970 | −312% |
| B1 fewest in flight | 10 | 71 | 2483 | 1897 | — |
| B2 per-tool mean run time (no arguments) | 10 | 72 | 2503 | 1905 | −1.2% [−4.7, 2.1] |
| **Method v1** | 10 | 73 | **2085** | **1503** | **16.1% [13.2, 19.5]**, 10/10 runs |
| Oracle (true run times) | 10 | 75 | 2079 | 1466 | 16.5% [13.4, 19.5] |

- B2 uses the same wait formula without the arguments and does not beat B1, so the gain
  comes from reading the arguments.
- The oracle is almost equal to the method, so a better predictor would add little here.
- Predictor (check 1, `run_sql`, held out by task): R² 0.88 on log run time, 0.54 on
  seconds; held out by template: 0.62 / 0.10.
- Per-call steering needs a new connection per call; setup costs 0.3 / 10 / 31 ms at
  MEC-1 / 2 / 3 (`results/conn_cost.csv`).

The load × skew sweep (`harness/sweep.sh`, figure `results/figs/sweep*.png`) shows where
steering pays off and where B0 is enough.

## Testbed (one host)

- **5G core:** free5GC v4.3.0 (free5gc-compose) + UERANSIM gNB and 30 UEs in docker,
  CPUs 0,1,8,9. gtp5g must be v0.10.2 (UPF v4.3.0 rejects 0.10.3). Start the SMF after
  the UPF. UE config: one PDU session, NSSAI 1/010203 only (`core/uecfg.yaml`).
- **MEC sites:** incus VMs mec1..mec3 (4 vCPU = 2 physical cores, 16 GiB each), bridges
  mecNbr 10.201.N.0/24, replica at 10.201.N.10. Distance from the UPF set with netem
  (`core/netem.sh`); measured ICMP RTT from a UE 1.65 / 11.85 / 29.7 ms
  (`results/rtt_table.md`).
- **Replicas:** MCP SQL server (DuckDB 1.4.1, TPC-H SF10, 2 workers, port 8000) and MCP
  KV server (5 ms lookup, port 8001); Streamable HTTP, stateless (mcp 1.20,
  pydantic 2.11.9).
- **Steering:** nftables in the UPF namespace (`core/steer_init.sh`). Anycast
  10.200.0.10:8000 (SQL) and 10.200.0.11:8001 (KV); each new connection draws a bucket
  0-99 and the split map sends it to a replica; conntrack keeps the connection there.
  `core/controller.py` rewrites the maps every 100 ms from the replicas' UDP status
  datagrams.

## Policies (`core/controller.py --policy`)

| Name | Signal per site |
|---|---|
| `b0` | nearest site only |
| `b1` | calls in flight, ties to the nearest |
| `b2` | wait from the per-tool mean run time + RTT |
| `method` | wait from the per-call predicted run time + RTT |
| `oracle` | wait from the measured run time of each call + RTT (ablation) |

All policies use the same water-filling split, period and horizon; only the signal changes.

## Reproduce

Paths are relative to the repo on the testbed host (`~/mcp_steer/repo`); the image
`mcp-steer:0.1` holds the Python dependencies.

1. **Sites.** In each VM: `mec/setup_vm.sh <site> <controller host:port> 2`, then
   `python mec/gen_data.py --sf 10 --out /data10 --no-kv` and set `DATA=/data10` in the
   `mcp-sql` service. On the host: `core/netem.sh`, `core/steer_init.sh`.
2. **RTT table.** `docker run --rm --network container:ueransim -v $PWD:/app mcp-steer:0.1 python tools/rtt_table.py --src 10.60.0.1 --out results/rtt_table`
3. **Record the agent** (Qwen3-8B on vLLM, OpenAI-compatible API):
   `python agent/agent_record.py --llm <url> --model <name> --out results/chains_sf10.jsonl ...`
   (50 tasks in `agent/tasks.py`, 10 templates × 5 parameter sets). The GPU machine is
   started and stopped with `harness/gpu_teardown.sh up|down` (set `GPU_INSTANCE_ID`).
4. **Check 1 and model install.** `harness/after_record.sh results/chains_sf10.jsonl results/rec_sf10 --install`
   (joins the replica logs, trains the predictor, writes R², installs the model, B2 means
   and oracle table on every replica).
5. **One matrix of runs.**
   ```
   harness/run_matrix.sh w13 "b0 b1 b2 method oracle" "$(seq 1 10)" results/rec_sf10/chains_joined.jsonl \
     --tasks results/rec_sf10/check1/test_tasks.txt --clients 90 --think-scale 0.2 \
     --capacity 4.3 --bg-workers 1.43 --load 0.7 --bg-load 0.3
   python harness/w13_stats.py w13 results/runs/w13_stats.json
   ```
6. **Connection cost.** `python tools/conn_cost.py --src 10.60.0.1 --out results/conn_cost` (UE netns).
7. **Sweep.** `harness/sweep.sh`, then `python harness/sweep_stats.py results/runs/sw_stats.json`
   and `python report/plot_sweep.py results/runs/sw_stats.json results/figs/sweep.png`.
8. **Reports.** `report/make_w12.py`, `report/make_w13.py` write the HTML pages of the
   course checks.

## Layout

| Folder | Content |
|---|---|
| `mec/` | replica code: FIFO workers, wait estimates, status datagrams, data generation |
| `core/` | 5G core config, steering maps, controller |
| `agent/` | tasks, agent recorder, joins of client and replica logs, oracle table |
| `predict/` | features from call arguments, training and check 1 |
| `harness/` | replay of recorded chains, run scripts, stats |
| `tools/` | RTT table and connection cost |
| `report/` | course report pages and figures |
| `results/` | recorded chains, check 1, per-run logs (`runs/<tag>_<policy>_s<seed>/`), tables |

## Notes from running it

- When all three sites are busy, long scans run about 1.4× slower (the three VMs share the
  host's memory bandwidth), so the measured capacity is 4.3 workers, not 6.
- With two workers, "remaining work / workers" is a wrong wait estimate (one long call
  next to an idle worker means no wait); the queue must be replayed per worker.
- Sending every call of an interval to the best-looking site makes herds; the split must
  be spread (water-filling) for every policy alike.
- MCP SDK 1.20 in stateless mode logs a harmless `ClosedResourceError` per request; its
  rich traceback costs 50-100 ms CPU per call, so the replicas turn it off.
