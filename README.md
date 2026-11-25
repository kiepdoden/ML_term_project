# mcp_steer: wait-aware steering of MCP tool calls across 5G MEC sites

Testbed (host 192.168.1.101, one machine):
- 5G core: free5GC v4.3.0 (free5gc-compose) + UERANSIM gNB and 30 UEs, docker, CPUs 0,1,8,9.
  gtp5g must be v0.10.2 (UPF v4.3.0 rejects 0.10.3). Start SMF after the UPF is up.
  UE config: one PDU session, NSSAI 1/010203 only (`core/uecfg.yaml`).
- Three MEC sites: incus VMs mec1..mec3 (4 vCPU = 2 physical cores each, 8 GiB),
  bridges mecNbr 10.201.N.0/24, replica at 10.201.N.10. RTT from the UPF set with netem
  (`core/netem.sh`): MEC-1 0 ms, MEC-2 10 ms, MEC-3 28 ms.
- Each site: MCP SQL server (DuckDB, TPC-H SF1, 2 single-thread workers, port 8000) and
  MCP KV server (5 ms lookup, port 8001); both Streamable HTTP, stateless.
- Steering: nft map in the UPF netns (`core/steer_init.sh`): anycast 10.200.0.10:8000 (SQL),
  10.200.0.11:8001 (KV) -> one replica per new connection. `core/controller.py` rewrites
  the map every 100 ms from the replicas' status datagrams (B0, B1, B2, method, oracle).

Layout: `mec/` replica code, `core/` 5G core config + controller, `agent/` tasks and
agent recorder, `harness/` replay, run and summary scripts, `tools/` RTT table, `results/`.
