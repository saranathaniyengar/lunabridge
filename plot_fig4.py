import json
import matplotlib.pyplot as plt

with open("results/fig4_results.json") as f:
    data = json.load(f)

breakdown = data["breakdown"]
policies = list(breakdown.keys())
classes = ["emergency", "telemetry", "sci_bulk", "media"]
states = ["delivered", "ttl_expired", "queue_overflow", "never_scheduled"]
state_colors = {"delivered": "#2ca02c", "ttl_expired": "#d62728",
                "queue_overflow": "#9467bd", "never_scheduled": "#555555"}

fig, axes = plt.subplots(2, 4, figsize=(20, 9))
axes_flat = axes.flatten()

for i, policy in enumerate(policies):
    ax = axes_flat[i]
    bottoms = [0] * len(classes)
    for state in states:
        vals = [breakdown[policy].get(c, {}).get(state, 0) for c in classes]
        ax.bar(classes, vals, bottom=bottoms, label=state, color=state_colors[state])
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.set_title(policy, fontsize=11, fontweight="bold")
    ax.tick_params(axis="x", rotation=30)
    if i == 0:
        ax.set_ylabel("bundle count")

axes_flat[-1].axis("off")
axes_flat[0].legend(loc="upper left", fontsize=8, bbox_to_anchor=(1.05, 1), ncol=1)

fig.suptitle(f"Fig 4 -- Triage map at R={data['meta']['fixed_r']} (stress plan, 10 kbps)\n"
             f"terminal-state breakdown per class, per policy ({data['meta']['n_bundles']:,} bundles)",
             fontsize=13, y=1.02)
fig.tight_layout()
fig.savefig("figures/fig4_triage_map.png", dpi=150, bbox_inches="tight")
print("saved figures/fig4_triage_map.png")
