import json
import matplotlib.pyplot as plt

with open("results/fig3_results.json") as f:
    data = json.load(f)

cluster = ["strict_priority", "wfq", "wfq_skip_over", "utility_aware", "utility_pure"]
colors = {"strict_priority": "#1f77b4", "wfq": "#2ca02c", "wfq_skip_over": "#17becf",
          "utility_aware": "#9467bd", "utility_pure": "#8c564b"}

r_values = sorted(int(r) for r in data["stress_plan"].keys())
fig, ax = plt.subplots(figsize=(8, 5.5))
for p in cluster:
    ys = [data["stress_plan"][str(r)][p] for r in r_values]
    mins = [min(data["stress_plan"][str(r)][pp] for pp in cluster) for r in r_values]
    rel = [y - m for y, m in zip(ys, mins)]
    ax.plot(r_values, rel, marker="o", label=p, color=colors[p], linewidth=2)

ax.set_xlabel("R (arrival-rate multiplier)")
ax.set_ylabel("mission_utility, relative to cluster minimum at each R")
ax.set_title("Zoom: the 5 closely-clustered policies\n(same stress-plan data as Fig 3, tight y-axis)")
ax.grid(alpha=0.3)
ax.legend(fontsize=9)
fig.tight_layout()
fig.savefig("figures/fig3_zoom_cluster.png", dpi=150, bbox_inches="tight")
print("saved figures/fig3_zoom_cluster.png")
