import json, time
from gateway.sweep_harness import sweep_ttl
from gateway.scheduler import SchedulingPolicy
from gateway.traffic import TrafficClass, CLASS_SPECS

CSV_PATH = "gateway/lcrns_relay_contact_plan_1sv.csv"
FIXED_R = 20  # clean, understood zone from Fig 3 (R=1-40, zero overflow)
MULTIPLIERS = [0.1, 0.316, 1, 3.16, 10]  # locked value always included (x1)
policies = [p.value for p in SchedulingPolicy]

CLASSES = [TrafficClass.EMERGENCY, TrafficClass.TELEMETRY,
           TrafficClass.SCIENCE_BULK, TrafficClass.MEDIA]

output = {"meta": {"multipliers": MULTIPLIERS, "fixed_r": FIXED_R,
                    "policies": policies,
                    "locked_ttls": {tc.value: CLASS_SPECS[tc].default_ttl_s for tc in CLASSES}},
          "real_plan": {}, "stress_plan": {}}

for tc in CLASSES:
    locked = CLASS_SPECS[tc].default_ttl_s
    ttl_values = [locked * m for m in MULTIPLIERS]

    t0 = time.time()
    real = sweep_ttl(tc, ttl_values, CSV_PATH, rate_multiplier=FIXED_R, stress=False)
    output["real_plan"][tc.value] = {
        ttl: {p: real[ttl][p]["mission_utility"] for p in policies} for ttl in ttl_values
    }
    print(f"{tc.value:12s} real_plan   done in {time.time()-t0:.1f}s")

    t0 = time.time()
    stress = sweep_ttl(tc, ttl_values, CSV_PATH, rate_multiplier=FIXED_R,
                        stress=True, stress_rate_bps=10_000.0)
    output["stress_plan"][tc.value] = {
        ttl: {p: stress[ttl][p]["mission_utility"] for p in policies} for ttl in ttl_values
    }
    print(f"{tc.value:12s} stress_plan done in {time.time()-t0:.1f}s")

with open("results/fig8_results.json", "w") as f:
    json.dump(output, f, indent=2)
print("saved results/fig8_results.json")
