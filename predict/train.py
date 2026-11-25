#!/usr/bin/env python3
"""Check 1: how much of a call's run time do its arguments explain?

Input: the joined call table (agent/join_runs.py -> *.calls.csv). Features
come only from the arguments (predict/features.py, EXPLAIN on the replica DB).
Held-out evaluation, two splits:
  by task      GroupKFold over the 50 task ids     (the proposal's split)
  by template  GroupKFold over the 10 templates    (stricter: unseen task types)
Reported for run_sql and for all tools together:
  R2 on log run time, R2 on seconds, MAE (ms), pinball loss at q=0.5 and 0.9,
  and the same for B2 (per-tool mean of the training folds, no arguments).
Then fits the final models on all calls and saves them for the replicas.
Usage (inside a MEC VM, it needs the DuckDB file for EXPLAIN):
  python train.py calls.csv out_dir
"""
import json
import os
import pickle
import sys

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold

sys.path.insert(0, os.path.dirname(__file__))
from features import call_features  # noqa: E402

calls_csv, out_dir = sys.argv[1], sys.argv[2]
db = os.environ.get("DB", "/data10/tpch.duckdb")
os.makedirs(out_dir, exist_ok=True)

df = pd.read_csv(calls_csv)
df = df[df["run_s"].notna() & (df["run_s"] > 0)].reset_index(drop=True)
con = duckdb.connect(db, read_only=True, config={"threads": 1})
feats = [call_features(con, t, json.loads(a)) for t, a in zip(df["tool"], df["args"])]
X = pd.DataFrame(feats).fillna(0.0)
y = np.log(df["run_s"].values)
df.join(X).to_csv(os.path.join(out_dir, "dataset.csv"), index=False)

PARAMS = dict(n_estimators=400, learning_rate=0.05, num_leaves=15, min_child_samples=10,
              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1)


def pinball(y_true, y_pred, q):
    d = y_true - y_pred
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def evaluate(mask, groups, name):
    Xs, ys, g, tools = X[mask], y[mask], groups[mask], df["tool"][mask].values
    pred = np.zeros(len(ys))
    p50 = np.zeros(len(ys))
    p90 = np.zeros(len(ys))
    b2 = np.zeros(len(ys))
    sec = np.zeros(len(ys))
    k = min(5, len(np.unique(g)))
    for tr, te in GroupKFold(n_splits=k).split(Xs, ys, g):
        m = lgb.LGBMRegressor(**PARAMS).fit(Xs.iloc[tr], ys[tr])
        pred[te] = m.predict(Xs.iloc[te])
        # a log model predicts the geometric mean; scale to seconds with the
        # smearing factor of the TRAINING folds (Duan 1983), as the replicas do
        sec[te] = np.exp(pred[te]) * np.mean(np.exp(ys[tr] - m.predict(Xs.iloc[tr])))
        p50[te] = lgb.LGBMRegressor(objective="quantile", alpha=0.5, **PARAMS).fit(
            Xs.iloc[tr], ys[tr]).predict(Xs.iloc[te])
        p90[te] = lgb.LGBMRegressor(objective="quantile", alpha=0.9, **PARAMS).fit(
            Xs.iloc[tr], ys[tr]).predict(Xs.iloc[te])
        sec_tr = pd.Series(np.exp(ys[tr])).groupby(tools[tr]).mean()   # B2: per-tool mean
        b2[te] = [sec_tr.get(t, np.exp(ys[tr]).mean()) for t in tools[te]]
    s_true = np.exp(ys)
    return {
        "split": name, "n": int(len(ys)),
        "r2_log": r2_score(ys, pred),
        "r2_sec": r2_score(s_true, sec),
        "mae_ms": 1000 * mean_absolute_error(s_true, sec),
        "pinball50_log": pinball(ys, p50, 0.5), "pinball90_log": pinball(ys, p90, 0.9),
        "b2_r2_log": r2_score(ys, np.log(b2)), "b2_r2_sec": r2_score(s_true, b2),
        "b2_mae_ms": 1000 * mean_absolute_error(s_true, b2),
    }


rows = []
sql = (df["tool"] == "run_sql").values
alltools = np.ones(len(df), dtype=bool)
for mask, what in [(sql, "run_sql"), (alltools, "all tools")]:
    for groups, split in [(df["task"].values, "by task"), (df["template"].values, "by template")]:
        r = evaluate(mask, groups, split)
        r["calls"] = what
        rows.append(r)
res = pd.DataFrame(rows)
res.to_csv(os.path.join(out_dir, "check1_r2.csv"), index=False)
pd.set_option("display.width", 200)
print(res.round(3).to_string(index=False))

# run time spread (for the paper and the revision note)
q = df.groupby("tool")["run_s"].describe(percentiles=[.5, .9, .99])
q.to_csv(os.path.join(out_dir, "runtime_by_tool.csv"))
print((q * 1000).round(1).to_string())

# fixed split for the experiments: variants 3 and 4 of every template are the
# test tasks (their chains are replayed); models are fitted on the rest only
test = df["task"].str.endswith(("-3", "-4")).values
train = ~test
m = lgb.LGBMRegressor(**PARAMS).fit(X[train], y[train])
smear = float(np.mean(np.exp(y[train] - m.predict(X[train]))))
pt = m.predict(X[test])
sql_t = (df["tool"] == "run_sql").values[test]
fixed = {"train_calls": int(train.sum()), "test_calls": int(test.sum()),
         "r2_log_all": r2_score(y[test], pt), "r2_sec_all": r2_score(np.exp(y[test]), np.exp(pt) * smear),
         "r2_log_sql": r2_score(y[test][sql_t], pt[sql_t]),
         "r2_sec_sql": r2_score(np.exp(y[test][sql_t]), np.exp(pt[sql_t]) * smear)}
print("fixed split:", json.dumps({k: round(v, 3) for k, v in fixed.items()}))
with open(os.path.join(out_dir, "fixed_split.json"), "w") as f:
    json.dump(fixed, f, indent=1)
with open(os.path.join(out_dir, "test_tasks.txt"), "w") as f:
    f.write("\n".join(sorted(df["task"][test].unique())))
final = {"columns": list(X.columns), "mean": m, "smear": smear}
with open(os.path.join(out_dir, "runtime_model.pkl"), "wb") as f:
    pickle.dump(final, f)
b2 = df[train].groupby("tool")["run_s"].mean().to_dict()
b2["_default"] = float(df[train]["run_s"].mean())
with open(os.path.join(out_dir, "b2_sql.json"), "w") as f:
    json.dump({k: v for k, v in b2.items() if k != "kv_get"} | {"_default": b2["_default"]}, f)
with open(os.path.join(out_dir, "b2_kv.json"), "w") as f:
    json.dump({"kv_get": b2.get("kv_get", 0.005), "_default": b2.get("kv_get", 0.005)}, f)
print("saved models to", out_dir)
