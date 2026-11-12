"""Plot measured network state times and the paired replay trade-off."""

from pathlib import Path
from statistics import fmean
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "figures"
OUT.mkdir(exist_ok=True)
data = json.loads((ROOT / "results" / "summary.json").read_text())
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.labelcolor": "#213144", "text.color": "#213144",
    "xtick.color": "#43566c", "ytick.color": "#43566c", "axes.edgecolor": "#b9c8d4",
    "grid.color": "#dbe4ea", "savefig.facecolor": "white",
})

paired = {x["policy"]: x for x in data["groups"] if x["scenario"] == "paired"}
keys = ["raw", "fixed", "rule", "jev-heuristic", "jev-reference", "jev-all", "jev-gated"]
labels = ["Raw", "Fixed", "Rule", "JEV heuristic", "JEV reference", "JEV all", "JEV gated"]
colors = ["#8599a9", "#4a708e", "#4a708e", "#337f78", "#779990", "#6260a5", "#ba6335"]
fig, axes = plt.subplots(1, 2, figsize=(7.1, 2.85), layout="constrained")
for ax, metric, scale, title in (
    (axes[0], "uplink_bytes", 0.001, "Backhaul per trace (kB)"),
    (axes[1], "cloud_count_coverage", 100, "Samples represented by query (%)"),
):
    values = [paired[key][metric] * scale for key in keys]
    ax.barh(labels, values, color=colors, height=0.57, zorder=3)
    ax.invert_yaxis()
    ax.set_title(title, loc="left", fontweight="bold", pad=10)
    ax.set_xlim(0, max(values) * 1.18)
    ax.grid(axis="x", zorder=0)
    ax.tick_params(axis="both", length=0)
    for index, value in enumerate(values):
        ax.text(value + max(values) * 0.02, index, f"{value:.1f}", va="center", fontsize=8)
axes[1].tick_params(labelleft=False)
fig.savefig(OUT / "gateway-tradeoff.png", dpi=200)
plt.close(fig)

network = {(x["topology"], x["seed"], x["stride"]): x for x in data["network"]}
fig, axes = plt.subplots(1, 2, figsize=(7.1, 2.45), layout="constrained")
for ax, metric, title in (
    (axes[0], "attempted", "Source datagrams attempted"),
    (axes[1], "sensor_tx_s", "Summed sensor radio TX (s)"),
):
    for topology, color in (("line", "#327f78"), ("mesh", "#6573a1"), ("bottleneck", "#b5663d")):
        for seed in (17, 29, 41):
            ax.plot([0, 1], [network[topology, seed, stride][metric] for stride in (1, 3)],
                    marker="o", markersize=3, color=color, alpha=0.7, linewidth=1.0)
        means = [fmean(network[topology, seed, stride][metric] for seed in (17, 29, 41))
                 for stride in (1, 3)]
        ax.plot([0, 1], means, color=color, linewidth=2.4, label=topology.title())
    ax.set_xticks([0, 1], ["Stride 1", "Stride 3"])
    ax.set_xlim(-0.15, 1.15)
    ax.set_title(title, loc="left", fontweight="bold", pad=10)
    ax.grid(axis="y")
    ax.tick_params(axis="both", length=0)
axes[1].legend(loc="upper right", fontsize=7, frameon=False)
fig.savefig(OUT / "network-reporting.png", dpi=200)
plt.close(fig)
