#!/usr/bin/env python3
"""Crossover figure for the load x skew sweep (from harness/sweep_stats.py).

Rows: median of all calls, p95 of short calls, p95 of all calls. Columns: skew (share of MEC-1
used by local users). x: offered load. One line per policy; the crossover
(the load above which the method beats staying at the nearest site, B0) is
marked with a dashed line in each row where it falls inside the sweep.
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
    "b1": ("B1 fewest in-flight", "#eb6834", "s"),
    "b2": ("B2 per-tool mean", "#1baf7a", "^"),
    "b0": ("B0 nearest", "#eda100", "D"),
}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
skews = sorted({c["skew"] for c in cells})
rows = [("p50", "median, all calls (s)"), ("short_p95", "p95, short calls (s)"),
        ("p95", "p95, all calls (s)")]

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                     "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED})
fig, axes = plt.subplots(len(rows), len(skews), figsize=(3.0 * len(skews) + 1.4, 7.6),
                         sharex=True, sharey="row", squeeze=False)


def crossover(xs, a, b):
    """First load where series a drops below series b (linear interpolation
    between sweep points); None if a never drops below b, or is below b
    already at the lowest load (no crossover inside the sweep)."""
    for i in range(1, len(xs)):
        d0, d1 = a[i - 1] - b[i - 1], a[i] - b[i]
        if d0 >= 0 > d1:
            return xs[i - 1] + (xs[i] - xs[i - 1]) * d0 / (d0 - d1)
    return None


for j, k in enumerate(skews):
    cs = sorted((c for c in cells if c["skew"] == k), key=lambda c: c["load"])
    xs = [100 * c["load"] for c in cs]
    for i, (m, label) in enumerate(rows):
        ax = axes[i][j]
        series = {}
        for pol, (name, col, mk) in STYLE.items():
            pts = [(100 * c["load"], c["policies"][pol][m]) for c in cs if pol in c["policies"]]
            if not pts:
                continue
            series[pol] = pts
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color=col, lw=2, marker=mk,
                    ms=6, mec="white", mew=1.2, label=name, zorder=3 if pol == "method" else 2)
        if "method" in series and "b0" in series and len(series["method"]) == len(series["b0"]):
            x = crossover([p[0] for p in series["method"]], [p[1] for p in series["method"]],
                          [p[1] for p in series["b0"]])
            if x is not None:
                ax.axvline(x, color=MUTED, lw=1, ls=(0, (4, 3)), zorder=1)
                ax.annotate(f"crossover\n≈{x:.0f}%", xy=(x, 1), xycoords=("data", "axes fraction"),
                                xytext=(4, -4), textcoords="offset points", va="top",
                                fontsize=8, color=MUTED)
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        ax.grid(True, which="major", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        if i == 0:
            ax.set_title(f"MEC-1 local users: {100 * k:.0f}%", fontsize=9.5, color=INK)
        if i == len(rows) - 1:
            ax.set_xlabel("offered load (% of capacity)")
            ax.set_xticks(xs)
        if j == 0:
            ax.set_ylabel(label)

handles, labels = axes[0][0].get_legend_handles_labels()
fig.legend(handles, labels, loc="upper center", ncol=len(labels), frameon=False,
           bbox_to_anchor=(0.5, 1.0), fontsize=9)
fig.tight_layout(rect=(0, 0, 1, 0.94))
fig.savefig(out, dpi=200)
print("wrote", out)
