import json
import matplotlib.pyplot as plt

with open("results/fig8_results.json") as f:
    data = json.load(f)

policies = data["meta"]["policies"]
locked_ttls = data["meta"]["locked_ttls"]
classes = list(locked_ttls.keys())

colors = {"fifo": "#999999", "strict_priority": "#1f77b4", "wfq": "#2ca02c",
          "wfq_skip_over": "#17becf", "deadline_aware": "#d62728",
          "utility_aware": "#9467bd", "utility_pure": "#8c564b"}

fig, axes = plt.subplots(2, 4, figsize=(20, 9), sharex="col")

for row, (plan_key, row_label) in enumerate([
    ("real_plan", "Real 90-day LCRNS plan (R=20)"),
    ("stress_plan", "Stress plan, 10 kbps cap (R=20)"),
]):
    for col, tc in enumerate(classes):
        ax = axes[row, col]
        ttl_values = sorted(float(t) for t in data[plan_key][tc].keys())
        for p in policies:
            ys = [data[plan_key][tc][str(t) if str(t) in data[plan_key][tc] else t][p]
                  if str(t) in data[plan_key][tc] else data[plan_key][tc][t][p]
                  for t in ttl_values]
            ax.plot(ttl_values, ys, marker="o", label=p, color=colors.get(p), linewidth=1.8, markersize=4)
        ax.axvline(locked_ttls[tc], color="black", linestyle=":", linewidth=1, alpha=0.6)
        ax.set_xscale("log")
        ax.axhline(0, color="black", linewidth=0.5, linestyle="-", alpha=0.3)
        ax.grid(alpha=0.25)
        if row == 0:
            ax.set_title(tc.upper(), fontsize=12, fontweight="bold")
        if col == 0:
            ax.set_ylabel(f"{row_label}\nmission_utility (U)", fontsize=10)
        if row == 1:
            ax.set_xlabel("TTL (s, log scale)", fontsize=9)

axes[0, -1].legend(loc="upper left", fontsize=7, bbox_to_anchor=(1.02, 1))
fig.suptitle("Fig 8 -- Mission utility vs. per-class TTL sensitivity (R=20 fixed)", fontsize=14, y=1.01)
fig.tight_layout()
fig.savefig("figures/fig8_ttl_sensitivity.png", dpi=150, bbox_inches="tight")
print("saved figures/fig8_ttl_sensitivity.png")
