import json

with open("results/fig3_results.json") as f:
    data = json.load(f)

policies = data["meta"]["policies"]

def render(plan_key, label):
    lines = [f"## {label}", "", "Raw mission_utility:", "",
             "| R | " + " | ".join(policies) + " |",
             "|" + "---|" * (len(policies) + 1)]
    r_values = sorted(int(r) for r in data[plan_key].keys())
    for r in r_values:
        row = data[plan_key][str(r)]
        lines.append(f"| {r} | " + " | ".join(f"{row[p]:,.0f}" for p in policies) + " |")
    lines += ["", "Ranked (#1 = best) with delta from best:", "",
              "| R | " + " | ".join(policies) + " |",
              "|" + "---|" * (len(policies) + 1)]
    for r in r_values:
        row = data[plan_key][str(r)]
        best = max(row.values())
        ranked = sorted(policies, key=lambda p: -row[p])
        rank_of = {p: i + 1 for i, p in enumerate(ranked)}
        lines.append(f"| {r} | " + " | ".join(
            f"#{rank_of[p]} ({row[p]-best:+,.0f})" for p in policies) + " |")
    return "\n".join(lines)

output = render("real_plan", "Real 90-day LCRNS plan") + "\n\n" + render("stress_plan", "Stress plan")
with open("results/fig3_table.md", "w") as f:
    f.write(output)
print(output)
