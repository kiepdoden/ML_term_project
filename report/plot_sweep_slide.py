#!/usr/bin/env python3
"""Slide version of the sweep: only the p95 gain over B1, large type, one
panel per share of local users at MEC-1. Usage: plot_sweep_slide.py <sw_stats.json> <out.png>"""
import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

cells = json.load(open(sys.argv[1]))["cells"]
INK, MUTED, GRID, BG = "#14213D", "#5E6676", "#E2E0D8", "#F7F6F2"
SER = {"method": ("Method v1", "#2A78D6", "o", -1.0), "method2": ("Method v2", "#E87BA4", "P", 1.0)}
FLOOR, TOP = -60, 40
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 17, "axes.edgecolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.labelcolor": INK})
skews = sorted({c["skew"] for c in cells})
fig, axes = plt.subplots(1, len(skews), figsize=(16.64, 5.6), sharey=True, facecolor=BG)
for ax, k in zip(axes, skews):
    ax.set_facecolor(BG)
    cs = sorted((c for c in cells if c["skew"] == k), key=lambda c: c["load"])
    xs = [100 * c["load"] for c in cs]
    ax.axhspan(FLOOR, 0, color="#EFE3DC", zorder=0)
    ax.axhline(0, color=MUTED, lw=1.2, zorder=1)
    for pol, (name, col, mk, dx) in SER.items():
        have = [(x, c) for x, c in zip(xs, cs) if f"{pol}_vs_b1" in c["gain"]]
        if not have:
            continue
        g = [100 * c["gain"][f"{pol}_vs_b1"]["p95"]["mean"] for _, c in have]
        lo = [max(v - FLOOR, 0) if 100 * c["gain"][f"{pol}_vs_b1"]["p95"]["ci"][0] < FLOOR
              else v - 100 * c["gain"][f"{pol}_vs_b1"]["p95"]["ci"][0] for v, (_, c) in zip(g, have)]
        hi = [100 * c["gain"][f"{pol}_vs_b1"]["p95"]["ci"][1] - v for v, (_, c) in zip(g, have)]
        ax.errorbar([x + dx for x, _ in have], g, yerr=[lo, hi], color=col, lw=3, marker=mk, ms=10,
                    mec=BG, mew=1.5, elinewidth=1.4, capsize=3, label=name, zorder=3)
    ax.set_title(f"MEC-1 local users {100 * k:.0f}%", fontsize=19, color=INK, pad=10)
    ax.set_xticks(xs)
    ax.set_xlabel("offered load (%)")
    ax.set_ylim(FLOOR, TOP)
    ax.grid(True, axis="y", color=GRID, lw=1)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
axes[0].set_ylabel("p95 gain over B1 (%)")
axes[0].text(12, FLOOR + 3, "method behind B1", color=MUTED, fontsize=14, va="bottom")
axes[0].text(12, TOP - 3, "method ahead of B1", color=MUTED, fontsize=14, va="top")
h, lab = axes[1].get_legend_handles_labels()
fig.legend(h, lab, loc="upper right", ncol=2, frameon=False, fontsize=16, bbox_to_anchor=(0.995, 1.0))
fig.tight_layout(rect=(0, 0, 1, 0.93))
fig.savefig(sys.argv[2], dpi=200, facecolor=BG)
print("wrote", sys.argv[2])
