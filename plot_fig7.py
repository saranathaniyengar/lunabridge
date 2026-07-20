import json
import matplotlib.pyplot as plt

with open("results/fig7_results.json") as f:
    data = json.load(f)

durations = data["meta"]["durations_h"]
limit = data["meta"]["buffer_limit_mb"]
r_values = data["meta"]["r_values"]
colors = plt.cm.viridis([i / (len(r_values) - 1) for i in range(len(r_values))])

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

for r, c in zip(r_values, colors):
    ax1.plot(durations, data["occupancy_mb"][str(r)], marker="o", markersize=3,
             linewidth=2, label=f"R={r}", color=c)
ax1.axhline(limit, color="black", linestyle="--", linewidth=1.5, label=f"{limit} MB cap")
ax1.set_xlabel("Blackout duration (h)")
ax1.set_ylabel("Buffer occupancy (MB)")
ax1.set_title("Occupancy (capped at 256 MB)")
ax1.legend(fontsize=9)
ax1.grid(alpha=0.3)

for r, c in zip(r_values, colors):
    ax2.plot(durations, data["overflow_mb"][str(r)], marker="o", markersize=3,
              linewidth=2, label=f"R={r}", color=c)
ax2.set_xlabel("Blackout duration (h)")
ax2.set_ylabel("Cumulative overflow (MB, rejected)")
ax2.set_title("Data lost to overflow once buffer is full")
ax2.legend(fontsize=9)
ax2.grid(alpha=0.3)

fig.suptitle("Fig 7 — Buffer fill during blackout, with real admission-cap enforcement", fontsize=13)
fig.tight_layout()
fig.savefig("figures/fig7_buffer_fill.png", dpi=150, bbox_inches="tight")
print("saved figures/fig7_buffer_fill.png")
