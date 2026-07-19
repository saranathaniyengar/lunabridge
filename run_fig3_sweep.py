import json, os, time
from gateway.sweep_harness import sweep_R
from gateway.scheduler import SchedulingPolicy

CSV_PATH = "gateway/lcrns_relay_contact_plan_1sv.csv"
os.makedirs("results", exist_ok=True)

R_REAL = [1, 15, 30]
R_STRESS = [1, 5, 10, 15, 20, 25, 30]
policies = [p.value for p in SchedulingPolicy]

output = {"meta": {"r_values_real": R_REAL, "r_values_stress": R_STRESS,
                    "policies": policies, "real_link_rate_bps": 10_000_000.0,
                    "stress_link_rate_bps": 10_000.0}, "real_plan": {}, "stress_plan": {}}

t0 = time.time()
real = sweep_R(R_REAL, CSV_PATH, stress=False)
for r, res in real.items():
    output["real_plan"][r] = {p: res[p]["mission_utility"] for p in policies}
print(f"real_plan done in {time.time()-t0:.1f}s")

t0 = time.time()
stress = sweep_R(R_STRESS, CSV_PATH, stress=True, stress_rate_bps=10_000.0)
for r, res in stress.items():
    output["stress_plan"][r] = {p: res[p]["mission_utility"] for p in policies}
print(f"stress_plan done in {time.time()-t0:.1f}s")

with open("results/fig3_results.json", "w") as f:
    json.dump(output, f, indent=2)
print("saved results/fig3_results.json")
