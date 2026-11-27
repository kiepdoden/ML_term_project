#!/usr/bin/env python3
"""Crossover figure for the load x skew sweep (from harness/sweep_stats.py).

Columns: skew (share of MEC-1 used by local users). x: offered load.
Rows:
  1. median latency of all calls, one line per policy; dashed line = load above
     which the method beats staying at the nearest site (B0).
  2. p95 latency of all calls, one line per policy (same crossover marker).
  3. p95 gain over B1 (1 - policy / B1, paired by seed; bars = 95% bootstrap CI
     over seeds) for the method and B2; dashed line = load above which the
     method stays ahead of B1.
Usage: plot_sweep.py <sw_stats.json> <out.png>"""
import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

src, out = sys.argv[1], sys.argv[2]
cells = json.load(open(src))["cells"]
# categorical slots 1-4 of the reference palette, fixed per entity
STYLE = {
    "method": ("Method v1", "#2a78d6", "o"),
    "method2": ("Method v2", "#e87ba4", "P"),
    "b1": ("B1 fewest in flight", "#eb6834", "s"),
    "b2": ("B2 per-tool mean", "#1baf7a", "^"),
    "b0": ("B0 nearest", "#eda100", "D"),
}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
skews = sorted({c["skew"] for c in cells})
seeds = sorted({s for c in cells for p in c["policies"].values() for s in p["seeds"]})

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                     "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED})
fig, axes = plt.subplots(3, len(skews), figsize=(3.0 * len(skews) + 1.4, 8.4),
                         sharex=True, sharey="row", squeeze=False)


def first_below(xs, a, b):
    """First load where series a drops below b (interpolated); None if a never
    does, or is already below b at the lowest load."""
    for i in range(1, len(xs)):
        d0, d1 = a[i - 1] - b[i - 1], a[i] - b[i]
        if d0 >= 0 > d1:
            return xs[i - 1] + (xs[i] - xs[i - 1]) * d0 / (d0 - d1)
    return None


def stays_above_zero(xs, g):
    """Load above which g stays > 0 (interpolated from the last g <= 0);
    None if g > 0 everywhere or g <= 0 at the highest load."""
    last = max((i for i, v in enumerate(g) if v <= 0), default=None)
    if last is None or last == len(g) - 1:
        return None
    d0, d1 = g[last], g[last + 1]
    return xs[last] + (xs[last + 1] - xs[last]) * (-d0) / (d1 - d0)


def mark(ax, x, text):
    ax.axvline(x, color=MUTED, lw=1, ls=(0, (4, 3)), zorder=1)
    ax.annotate(f"{text}\n≈{x:.0f}% load", xy=(x, 1), xycoords=("data", "axes fraction"),
                xytext=(4, -4), textcoords="offset points", va="top", fontsize=7.5,
                color=MUTED)


def style(ax):
    ax.grid(True, which="major", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


for j, k in enumerate(skews):
    cs = sorted((c for c in cells if c["skew"] == k), key=lambda c: c["load"])
    xs = [100 * c["load"] for c in cs]
    # rows 1-2: latency levels
    for i, (m, scale) in enumerate((("p50", 1000.0), ("p95", 1.0))):
        ax = axes[i][j]
        series = {}
        for pol, (name, col, mk) in STYLE.items():
            have = [(x, c) for x, c in zip(xs, cs) if pol in c["policies"]]
            if not have:          # method v2 was run only in some cells
                continue
            ys = [scale * c["policies"][pol][m] for _, c in have]
            if len(have) == len(cs):
                series[pol] = ys
            ax.plot([x for x, _ in have], ys, color=col, lw=2, marker=mk, ms=6, mec="white", mew=1.2,
                    label=name, zorder=3 if pol == "method" else 2)
        if "method" in series and "b0" in series:
            x = first_below(xs, series["method"], series["b0"])
            if x is not None:
                mark(ax, x, "method beats B0")
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        style(ax)
    # row 3: paired gain over B1
    ax = axes[2][j]
    ax.axhline(0, color=MUTED, lw=1, zorder=1)
    for pol in ("method", "method2", "b2"):
        name, col, mk = STYLE[pol]
        key = f"{pol}_vs_b1"
        have = [(x, c) for x, c in zip(xs, cs) if key in c["gain"]]
        if not have:
            continue
        g = [100 * c["gain"][key]["p95"]["mean"] for _, c in have]
        lo = [g[n] - 100 * c["gain"][key]["p95"]["ci"][0] for n, (_, c) in enumerate(have)]
        hi = [100 * c["gain"][key]["p95"]["ci"][1] - g[n] for n, (_, c) in enumerate(have)]
        dx = {"method": -1.5, "method2": 0.0, "b2": 1.5}[pol]   # side by side, bars apart
        ax.errorbar([x + dx for x, _ in have], g, yerr=[lo, hi], color=col, lw=2, marker=mk, ms=6,
                    mec="white", mew=1.2, elinewidth=1, capsize=2, label=name,
                    zorder=3 if pol == "method" else 2)
        if pol == "method" and len(have) == len(cs):
            x = stays_above_zero(xs, g)
            if x is not None:
                mark(ax, x, "method ahead of B1")
    style(ax)
    ax.set_xlabel("offered load (% of capacity)")
    ax.set_xticks(xs)
    axes[0][j].set_title(f"MEC-1 local users: {100 * k:.0f}%", fontsize=9.5, color=INK)

axes[0][0].set_ylabel("median, all calls (ms)")
axes[1][0].set_ylabel("p95, all calls (s)")
axes[2][0].set_ylabel("p95 gain over B1 (%)")
GAIN_FLOOR = -80                  # one wide CI (down to -130%) would flatten the rest
axes[2][0].set_ylim(bottom=GAIN_FLOOR)
seen = {}                          # v2 appears only in the columns with local users
for ax in axes[0]:
    for h, lab in zip(*ax.get_legend_handles_labels()):
        seen.setdefault(lab, h)
order = [STYLE[p][0] for p in STYLE if STYLE[p][0] in seen]
handles, labels = [seen[lab] for lab in order], order
fig.legend(handles, labels, loc="upper center", ncol=len(labels), frameon=False,
           bbox_to_anchor=(0.5, 1.0), fontsize=9)
fig.text(0.5, 0.005, f"Mean over seeds {', '.join(map(str, seeds))}; gain bars: 95% bootstrap CI "
         f"over seeds, paired by seed, cut at {GAIN_FLOOR}%.", ha="center", va="bottom", fontsize=7.5, color=MUTED)
fig.tight_layout(rect=(0, 0.02, 1, 0.95))
fig.savefig(out, dpi=200)
print("wrote", out)
