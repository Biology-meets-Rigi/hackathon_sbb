"""Draw the Pareto front of the web app run of 2026-11-13 as three pairwise views (plan §4.6).

Usage, from the repo root (needs pandas and matplotlib):
    python docs/plans/maintenance-schedule-optimizer/pareto_pairs_chart.py
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
front = pd.read_csv(HERE / "pareto-front-2026-11-13.csv")
out = HERE / "pareto-pairs.png"
OBJ = ["served_adjusted", "avg_condition", "avg_travel_time", "total_cost"]
fits = front[OBJ].to_numpy(dtype=float)
lo, hi = fits.min(axis=0), fits.max(axis=0)
span = np.maximum(hi - lo, 1e-9)
ideal = np.array([hi[0], hi[1], lo[2], lo[3]])
picked_i = int(np.argmin(np.linalg.norm((fits - ideal) / span, axis=1)))

served = front["served_adjusted"]
cond = front["avg_condition"]
tt = front["avg_travel_time"] * 60
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE = "#2a78d6", "#eb6834"
plt.rcParams.update({"font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK_2,
                     "xtick.color": INK_2, "ytick.color": INK_2})

# (x, y, x label, y label, x sign, y sign): sign +1 = more is better
pairs = [
    (served, tt, "Served trips (more is better)", "Avg. travel time, min (less is better, inverted)", 1, -1),
    (served, cond, "Served trips (more is better)", "Avg. condition (more is better)", 1, 1),
    (cond, tt, "Avg. condition (more is better)", "Avg. travel time, min (less is better, inverted)", 1, -1),
]
fig, axes = plt.subplots(1, 3, figsize=(12, 4.0), dpi=150, facecolor=SURFACE)
for ax, (x, y, xl, yl, sx, sy) in zip(axes, pairs):
    xs, ys = x.to_numpy() * sx, y.to_numpy() * sy
    dominated = np.array([any((xs[j] >= xs[i]) and (ys[j] >= ys[i]) and ((xs[j] > xs[i]) or (ys[j] > ys[i]))
                              for j in range(len(xs)) if j != i) for i in range(len(xs))])
    keep = np.ones(len(xs), bool)
    keep[picked_i] = False
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    m = keep & ~dominated
    ax.scatter(x[m], y[m], s=52, color=BLUE, edgecolors=SURFACE, linewidths=1, zorder=3,
               label="on the front in this view")
    m = keep & dominated
    ax.scatter(x[m], y[m], s=48, facecolors=SURFACE, edgecolors=BLUE, linewidths=1.5, zorder=3,
               label="beaten in this view, kept by the third objective")
    ax.scatter(x[[picked_i]], y[[picked_i]], s=90, marker="D", color=ORANGE, edgecolors=SURFACE,
               linewidths=2, zorder=4, label="balanced schedule (picked)")
    ax.set_xlabel(xl)
    ax.set_ylabel(yl)
    # Better is always right and top: invert an axis where less is better.
    if sx < 0:
        ax.invert_xaxis()
    if sy < 0:
        ax.invert_yaxis()
    ax.set_title(f"{int(dominated.sum())} of {len(xs)} beaten in this view", loc="left", color=INK, fontsize=10)
handles, labels = axes[0].get_legend_handles_labels()
fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False, labelcolor=INK)
fig.suptitle("Pareto front of one web app run, three pairwise views, better is right and top (cost is the same for all 19 schedules)",
             x=0.01, ha="left", color=INK, fontsize=11)
fig.tight_layout(rect=(0, 0.08, 1, 0.95))
fig.savefig(out, facecolor=SURFACE)
