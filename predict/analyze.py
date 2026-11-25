#!/usr/bin/env python3
"""Why is R2 on seconds low while R2 on log time is high? Compare model
targets on the same held-out-by-task folds and look at the biggest errors.
Usage: analyze.py <check1 dir>   (reads dataset.csv written by train.py)"""
import json
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import r2_score, roc_auc_score
from sklearn.model_selection import GroupKFold

d = sys.argv[1]
df = pd.read_csv(f"{d}/dataset.csv")
meta = ["task", "template", "run", "step", "tool", "args", "run_s", "wait_s", "site", "err",
        "think_s"]
X = df.drop(columns=[c for c in meta if c in df.columns]).fillna(0.0)
y = df["run_s"].values
sql = (df["tool"] == "run_sql").values
P = dict(n_estimators=400, learning_rate=0.05, num_leaves=15, min_child_samples=10,
         subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1)
res = {}
preds = {k: np.zeros(sql.sum()) for k in ["log", "log_smear", "sec_l2", "sec_huber"]}
Xs, ys, g = X[sql].reset_index(drop=True), y[sql], df["task"].values[sql]
for tr, te in GroupKFold(5).split(Xs, ys, g):
    m = lgb.LGBMRegressor(**P).fit(Xs.iloc[tr], np.log(ys[tr]))
    p = m.predict(Xs.iloc[te])
    smear = np.mean(np.exp(np.log(ys[tr]) - m.predict(Xs.iloc[tr])))
    preds["log"][te] = np.exp(p)
    preds["log_smear"][te] = np.exp(p) * smear
    preds["sec_l2"][te] = lgb.LGBMRegressor(**P).fit(Xs.iloc[tr], ys[tr]).predict(Xs.iloc[te])
    preds["sec_huber"][te] = lgb.LGBMRegressor(objective="huber", alpha=1.0, **P).fit(
        Xs.iloc[tr], ys[tr]).predict(Xs.iloc[te])
long_ = ys >= 0.2
for k, p in preds.items():
    res[k] = {"r2_sec": r2_score(ys, p), "r2_log": r2_score(np.log(ys), np.log(np.clip(p, 1e-4, None))),
              "spearman": spearmanr(ys, p).statistic, "auc_long": roc_auc_score(long_, p),
              "r2_sec_clip5": r2_score(np.clip(ys, 0, 5), np.clip(p, 0, 5)),
              "sum_pred/sum_true": p.sum() / ys.sum()}
print(pd.DataFrame(res).T.round(3).to_string())
DEP = "log_smear"                     # the model the replicas use
r_all = r2_score(ys, preds[DEP])
i_max = int(np.argmax(ys))
keep = np.ones(len(ys), dtype=bool)
keep[i_max] = False
out = {"n_sql": int(len(ys)), "n_long": int(long_.sum()), "long_cut_s": 0.2,
       "max_run_s": float(ys[i_max]), "max_pred_s": float(preds[DEP][i_max]),
       "r2_sec_log_model": float(r_all),
       "r2_sec_without_max": float(r2_score(ys[keep], preds[DEP][keep])),
       "share_sse_max_call": float((ys[i_max] - preds[DEP][i_max]) ** 2 /
                                   np.sum((ys - preds[DEP]) ** 2)),
       "top1pct_work_share": float(np.sort(ys)[-max(1, len(ys) // 100):].sum() / ys.sum()),
       "models": {k: {m: float(v) for m, v in r.items()} for k, r in res.items()}}
json.dump(out, open(f"{d}/analysis.json", "w"), indent=1)
print(f"\nrun_sql calls {len(ys)}, long (>=0.2 s) {long_.sum()} ({100 * long_.mean():.1f}%), "
      f"share of all SQL work in the top 1% of calls: "
      f"{100 * np.sort(ys)[-max(1, len(ys) // 100):].sum() / ys.sum():.1f}%")
dd = df[sql].reset_index(drop=True).assign(pred=preds["log"])
top = dd.sort_values("run_s", ascending=False).head(12)
for _, r in top.iterrows():
    q = json.loads(r["args"]).get("query", "")[:150].replace("\n", " ")
    print(f"{r['run_s']:7.2f}s pred {r['pred']:6.2f}s err={str(r['err'])[:20]:20s} {q}")
