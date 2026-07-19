import json, time
from gateway.sweep_harness import sweep_ttl
from gateway.traffic import TrafficClass, CLASS_SPECS

CSV_PATH = "gateway/lcrns_relay_contact_plan_1sv.csv"
MULTIPLIERS = [0.1, 0.316, 1, 3.16, 10]
R_VALUES = [1, 5, 20]
CLASSES = [TrafficClass.EMERGENCY, TrafficClass.TELEMETRY,
           TrafficClass.SCIENCE_BULK, TrafficClass.MEDIA]

with open("results/fig8_results.json") as f:
    data = json.load(f)

policies = data["meta"]["policies"]
locked_ttls = data["meta"]["locked_ttls"]

real_by_r = {tc.value: {} for tc in CLASSES}

for tc in CLASSES:
    locked = locked_ttls[tc.value]
    ttl_values = [locked * m for m in MULTIPLIERS]
    for r in R_VALUES:
        t0 = time.time()
        result = sweep_ttl(tc, ttl_values, CSV_PATH, rate_multiplier=r, stress=False)
        real_by_r[tc.value][str(r)] = {
            ttl: {p: {"mission_utility": result[ttl][p]["mission_utility"],
                       "own_delivery_ratio": result[ttl][p]["delivery_ratio_by_class"].get(tc.value, None)}
                  for p in policies}
            for ttl in ttl_values
        }
        print(f"{tc.value:12s} real R={r:>3d} done in {time.time()-t0:.1f}s")

data["real_by_r"] = real_by_r
with open("results/fig8_results.json", "w") as f:
    json.dump(data, f, indent=2)
print("saved results/fig8_results.json (real_by_r added)")
