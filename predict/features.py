"""Features of one MCP call, computed from its arguments only.

The replica can read the arguments (the UPF cannot). For run_sql it also asks
DuckDB for the plan (EXPLAIN, no execution, a few ms) and uses the planner's
estimated cardinalities. Other tools get only their name.
"""
import json
import math
import re

TABLES = ["lineitem", "orders", "partsupp", "part", "customer", "supplier", "nation", "region"]
ROWS_SF10 = {"lineitem": 59986052, "orders": 15000000, "partsupp": 8000000, "part": 2000000,
             "customer": 1500000, "supplier": 100000, "nation": 25, "region": 5}
OPS = ["SEQ_SCAN", "TABLE_SCAN", "HASH_JOIN", "NESTED_LOOP_JOIN", "CROSS_PRODUCT",
       "PIECEWISE_MERGE_JOIN", "HASH_GROUP_BY", "PERFECT_HASH_GROUP_BY", "UNGROUPED_AGGREGATE",
       "ORDER_BY", "TOP_N", "FILTER", "WINDOW", "DELIM_JOIN", "STREAMING_LIMIT", "LIMIT"]
TOOLS = ["run_sql", "describe_table", "list_tables", "kv_get"]


def text_features(q):
    s = " " + re.sub(r"\s+", " ", q.lower()) + " "
    f = {
        "q_len": len(s),
        "n_select": s.count(" select "),
        "n_join": s.count(" join "),
        "n_and": s.count(" and "),
        "n_or": s.count(" or "),
        "n_agg": len(re.findall(r"\b(sum|avg|count|min|max)\s*\(", s)),
        "has_group": int(" group by " in s),
        "has_order": int(" order by " in s),
        "has_distinct": int(" distinct " in s),
        "has_like": int(" like " in s),
        "has_in": int(re.search(r" in \(", s) is not None),
        "has_date": int("date '" in s or "date'" in s),
        "has_window": int(" over (" in s or " over(" in s),
        "point_lookup": int(re.search(r"\w*key\s*=\s*\d+", s) is not None),
    }
    m = re.search(r" limit (\d+)", s)
    f["limit_log"] = math.log1p(int(m.group(1))) if m else -1.0
    used = [t for t in TABLES if re.search(rf"\b{t}\b", s)]
    f["n_tables"] = len(used)
    for t in TABLES:
        f[f"t_{t}"] = int(t in used)
    f["log_rows_tables"] = math.log1p(sum(ROWS_SF10[t] for t in used))
    return f


def _walk(node, out):
    name = node.get("name", "").strip().upper().replace(" ", "_")
    extra = node.get("extra_info", {}) or {}
    card = None
    if isinstance(extra, dict):
        v = extra.get("Estimated Cardinality")
        if v is not None:
            try:
                card = float(str(v).replace("~", "").replace(",", ""))
            except ValueError:
                card = None
    out.append((name, card))
    for c in node.get("children", []) or []:
        _walk(c, out)


def plan_features(con, q):
    """EXPLAIN (FORMAT JSON) on the replica's database; never executes q."""
    f = {"plan_ok": 0, "n_nodes": 0, "log_card_sum": 0.0, "log_card_max": 0.0,
         "log_scan_card": 0.0}
    for op in OPS:
        f[f"op_{op.lower()}"] = 0
    try:
        q = q.strip().rstrip(";")
        rows = con.execute(f"EXPLAIN (FORMAT JSON) {q}").fetchall()
        plan = json.loads(rows[0][1])
    except Exception:
        return f
    nodes = []
    for root in plan if isinstance(plan, list) else [plan]:
        _walk(root, nodes)
    cards = [c for _, c in nodes if c is not None]
    scans = [c for n, c in nodes if c is not None and ("SCAN" in n)]
    f["plan_ok"] = 1
    f["n_nodes"] = len(nodes)
    f["log_card_sum"] = math.log1p(sum(cards)) if cards else 0.0
    f["log_card_max"] = math.log1p(max(cards)) if cards else 0.0
    f["log_scan_card"] = math.log1p(sum(scans)) if scans else 0.0
    for n, _ in nodes:
        k = f"op_{n.lower()}"
        if k in f:
            f[k] += 1
    return f


def call_features(con, tool, args):
    f = {f"tool_{t}": int(tool == t) for t in TOOLS}
    if tool == "run_sql":
        q = (args or {}).get("query", "") or ""
        f.update(text_features(q))
        f.update(plan_features(con, q))
    return f
