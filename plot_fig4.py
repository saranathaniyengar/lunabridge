import json
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

plt.rcParams["font.size"] = 10
plt.rcParams["axes.spines.top"] = False
plt.rcParams["axes.spines.right"] = False

with open("results/fig4_results.json") as f:
    data = json.load(f)

breakdown = data["breakdown"]
policies = list(breakdown.keys())
classes = ["emergency", "telemetry", "sci_bulk", "media"]
class_labels = ["EMERGENCY", "TELEMETRY", "SCI_BULK", "MEDIA"]
states = ["delivered", "ttl_expired", "queue_overflow", "never_scheduled"]
state_labels = {"delivered": "Delivered", "ttl_expired": "TTL Expired",
                "queue_overflow": "Queue Overflow", "never_scheduled": "Never Scheduled"}
state_colors = {"delivered": "#2e8b57", "ttl_expired": "#c0392b",
                "queue_overflow": "#8e44ad", "never_scheduled": "#7f7f7f"}

fig, axes = plt.subplots(2, 4, figsize=(20, 9.5), sharey=True)
axes_flat = axes.flatten()

for i, policy in enumerate(policies):
    ax = axes_flat[i]
    bottoms = [0.0] * len(classes)
    totals = [sum(breakdown[policy].get(c, {}).values()) for c in classes]

    for state in states:
        pct_vals = [
            100 * breakdown[policy].get(c, {}).get(state, 0) / t if t else 0
            for c, t in zip(classes, totals)
        ]
        ax.bar(class_labels, pct_vals, bottom=bottoms, color=state_colors[state],
               edgecolor="white", linewidth=0.7, width=0.65)
        bottoms = [b + v for b, v in zip(bottoms, pct_vals)]

    for x, (t, c) in enumerate(zip(totals, classes)):
        delivered = breakdown[policy].get(c, {}).get("delivered", 0)
        pct = 100 * delivered / t if t else 0
        ax.text(x, 103, f"{pct:.0f}%", ha="center", fontsize=9.5,
                fontweight="bold", color=state_colors["delivered"])
        ax.text(x, -8, f"n={t:,}", ha="center", fontsize=7.5, color="#666666")

    ax.set_title(policy, fontsize=12, fontweight="bold", pad=14)
    ax.set_ylim(-14, 112)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.grid(axis="y", alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(axis="x", rotation=20, labelsize=9)
    if i % 4 == 0:
        ax.set_ylabel("% of that class's bundles", fontsize=10)

axes_flat[-1].axis("off")
legend_handles = [mpatches.Patch(color=state_colors[s], label=state_labels[s]) for s in states]
axes_flat[-1].legend(handles=legend_handles, loc="center", fontsize=11,
                      title="Terminal state", title_fontsize=11, frameon=False)

fig.suptitle(f"Fig 4 — Triage Map at R={data['meta']['fixed_r']} (stress plan, 10 kbps)",
             fontsize=15, fontweight="bold", y=1.015)
fig.text(0.5, 0.975, f"Each bar normalized to 100% of that class's own bundles  ·  "
          f"{data['meta']['n_bundles']:,} total bundles  ·  bold % = delivery rate",
          ha="center", fontsize=10.5, style="italic", color="#444444")
fig.tight_layout(rect=[0, 0, 1, 0.96])
fig.savefig("figures/fig4_triage_map.png", dpi=150, bbox_inches="tight", facecolor="white")
print("saved figures/fig4_triage_map.png")
