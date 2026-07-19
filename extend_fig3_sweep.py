import json, time
from gateway.sweep_harness import sweep_R
from gateway.scheduler import SchedulingPolicy

CSV_PATH = "gateway/lcrns_relay_contact_plan_1sv.csv"
NEW_R_VALUES = [75, 100, 150]  # extends past the R=50 compounding-overflow finding
policies = [p.value for p in SchedulingPolicy]

with open("results/fig3_results.json") as f:
    output = json.load(f)

t0 = time.time()
stress = sweep_R(NEW_R_VALUES, CSV_PATH, stress=True, stress_rate_bps=10_000.0)
for r, res in stress.items():
    output["stress_plan"][r] = {p: res[p]["mission_utility"] for p in policies}
print(f"new R values done in {time.time()-t0:.1f}s")

output["meta"]["r_values_stress"] = sorted(
    set(output["meta"]["r_values_stress"]) | set(NEW_R_VALUES)
)

with open("results/fig3_results.json", "w") as f:
    json.dump(output, f, indent=2)
print("merged and saved results/fig3_results.json")
