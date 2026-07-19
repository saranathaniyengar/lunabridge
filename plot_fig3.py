import json, os
import matplotlib.pyplot as plt

with open("results/fig3_results.json") as f:
    data = json.load(f)

policies = data["meta"]["policies"]
colors = {"fifo": "#999999", "strict_priority": "#1f77b4", "wfq": "#2ca02c",
          "wfq_skip_over": "#17becf", "deadline_aware": "#d62728",
          "utility_aware": "#9467bd", "utility_pure": "#8c564b"}

os.makedirs("figures", exist_ok=True)
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))
for ax, key, title in [(ax1, "real_plan", "Real 90-day LCRNS plan\n(10 Mbps -- bandwidth never binds)"),
                        (ax2, "stress_plan", "Stress plan\n(10 kbps cap, same real window timing)")]:
    r_values = sorted(int(r) for r in data[key].keys())
    for p in policies:
        ys = [data[key][str(r)][p] for r in r_values]
        ax.plot(r_values, ys, marker="o", label=p, color=colors.get(p), linewidth=2)
    ax.set_xlabel("R (arrival-rate multiplier)")
    ax.set_ylabel("mission_utility (U)")
    ax.set_title(title, fontsize=11)
    ax.axhline(0, color="black", linewidth=0.6, linestyle=":")
    ax.grid(alpha=0.3)
ax2.legend(loc="upper left", fontsize=8)
fig.suptitle("Fig 3 -- Mission utility vs. R, per scheduling policy", fontsize=13, y=1.02)
fig.tight_layout()
fig.savefig("figures/fig3_mission_utility_vs_R.png", dpi=150, bbox_inches="tight")
print("saved figures/fig3_mission_utility_vs_R.png")
