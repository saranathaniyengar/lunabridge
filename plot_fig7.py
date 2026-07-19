import json
import matplotlib.pyplot as plt

with open("results/fig7_results.json") as f:
    data = json.load(f)

durations = data["meta"]["durations_h"]
limit = data["meta"]["buffer_limit_mb"]

fig, ax = plt.subplots(figsize=(9, 6))
colors = {"1.5": "#1f77b4", "2.0": "#d62728"}
for r_str, occ in data["occupancy_mb"].items():
    ax.plot(durations, occ, marker="o", markersize=3, linewidth=2,
            label=f"R={r_str}", color=colors.get(r_str, "black"))

ax.axhline(limit, color="black", linestyle="--", linewidth=1.5,
           label=f"CM4 buffer limit ({limit} MB)")
ax.set_xlabel("Blackout duration (h)")
ax.set_ylabel("Buffer occupancy (MB)")
ax.set_title("Fig 7 — Buffer fill during blackout\n(TTL-aware: expired bundles removed from occupancy)")
ax.legend(fontsize=10)
ax.grid(alpha=0.3)
fig.tight_layout()
fig.savefig("figures/fig7_buffer_fill.png", dpi=150, bbox_inches="tight")
print("saved figures/fig7_buffer_fill.png")
