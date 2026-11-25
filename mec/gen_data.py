#!/usr/bin/env python3
"""Generate the site data: TPC-H SF1 in DuckDB (one file copy per worker) and
the key-value records. dbgen is deterministic, so every site gets the same data."""
import argparse
import json
import os
import shutil

import duckdb

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="/data")
ap.add_argument("--sf", type=float, default=1.0)
ap.add_argument("--workers", type=int, default=2)
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)

base = os.path.join(a.out, "tpch.duckdb")
if not os.path.exists(base):
    con = duckdb.connect(base)
    con.execute("INSTALL tpch; LOAD tpch;")
    con.execute(f"CALL dbgen(sf={a.sf})")
    con.execute("CHECKPOINT")
    con.close()

for i in range(a.workers):
    dst = os.path.join(a.out, f"tpch_w{i}.duckdb")
    if not os.path.exists(dst):
        shutil.copyfile(base, dst)

con = duckdb.connect(base, read_only=True)
kv = {}
for ck, name, phone, seg, bal, nat in con.execute(
        "SELECT c_custkey, c_name, c_phone, c_mktsegment, c_acctbal, n_name "
        "FROM customer JOIN nation ON c_nationkey = n_nationkey").fetchall():
    kv[f"customer:{ck}"] = {"name": name, "phone": phone, "segment": seg,
                            "acctbal": float(bal), "nation": nat}
for sk, name, phone, nat in con.execute(
        "SELECT s_suppkey, s_name, s_phone, n_name "
        "FROM supplier JOIN nation ON s_nationkey = n_nationkey").fetchall():
    kv[f"supplier:{sk}"] = {"name": name, "phone": phone, "nation": nat}
with open(os.path.join(a.out, "kv.json"), "w") as f:
    json.dump(kv, f)
print("rows:", {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ["customer", "orders", "lineitem"]}, "kv:", len(kv))
