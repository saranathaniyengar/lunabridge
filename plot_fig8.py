import json
import matplotlib.pyplot as plt

with open("results/fig8_results.json") as f:
    data = json.load(f)

policies = data["meta"]["policies"]
locked_ttls = data["meta"]["locked_ttls"]
classes = list(locked_ttls.keys())
r_values = [1, 5, 20]

colors = {"fifo": "#999999", "strict_priority": "#1f77b4", "wfq": "#2ca02c",
          "wfq_skip_over": "#17becf", "deadline_aware": "#d62728",
          "utility_aware": "#9467bd", "utility_pure": "#8c564b"}

fig, axes = plt.subplots(6, 4, figsize=(20, 24), sharex="col")

row_specs = [("real_by_r", r, f"R={r} (real plan)") for r in r_values] + \
            [("stress_by_r", r, f"R={r} (stress plan)") for r in r_values]

for row, (data_key, r, row_label) in enumerate(row_specs):
    for col, tc in enumerate(classes):
        ax = axes[row, col]
        by_r = data[data_key][tc][str(r)]
        ttl_values = sorted(float(t) for t in by_r.keys())
        for p in policies:
            ys = [by_r[str(t)][p]["own_delivery_ratio"] for t in ttl_values]
            ax.plot(ttl_values, ys, marker="o", label=p, color=colors.get(p), linewidth=1.8, markersize=4)
        ax.axvline(locked_ttls[tc], color="black", linestyle=":", linewidth=1, alpha=0.6)
        ax.set_xscale("log")
        ax.set_ylim(-0.05, 1.05)
        ax.grid(alpha=0.25)
        if row == 0:
            ax.set_title(tc.upper(), fontsize=12, fontweight="bold")
        if col == 0:
            ax.set_ylabel(f"{row_label}\nown-class delivery ratio", fontsize=9)
        if row == 5:
            ax.set_xlabel("TTL (s, log scale)", fontsize=9)

axes[0, -1].legend(loc="upper left", fontsize=7, bbox_to_anchor=(1.02, 1))
fig.suptitle("Fig 8 -- Per-class delivery ratio vs. TTL: real plan (top 3 rows) vs. stress plan (bottom 3 rows), R=1/5/20", fontsize=13, y=1.005)
fig.tight_layout()
fig.savefig("figures/fig8_ttl_sensitivity.png", dpi=150, bbox_inches="tight")
print("saved figures/fig8_ttl_sensitivity.png")
