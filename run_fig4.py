import json
from gateway.workload_generator import generate_workload
from gateway.sweep_harness import load_stress_contact_plan, compute_triage_breakdown

CSV_PATH = "gateway/lcrns_relay_contact_plan_1sv.csv"
FIXED_R = 20  # same clean zone as Fig 8

plan = load_stress_contact_plan(CSV_PATH, rate_bps_override=10_000.0)
bundles = generate_workload(seed=42, rate_multiplier=FIXED_R)

breakdown = compute_triage_breakdown(bundles, plan)

with open("results/fig4_results.json", "w") as f:
    json.dump({"meta": {"fixed_r": FIXED_R, "n_bundles": len(bundles)},
               "breakdown": breakdown}, f, indent=2)
print(f"saved results/fig4_results.json ({len(bundles)} bundles)")
