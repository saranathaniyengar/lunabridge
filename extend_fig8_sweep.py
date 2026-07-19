import json, time
from gateway.sweep_harness import sweep_ttl
from gateway.traffic import TrafficClass, CLASS_SPECS

CSV_PATH = "gateway/lcrns_relay_contact_plan_1sv.csv"
MULTIPLIERS = [0.1, 0.316, 1, 3.16, 10]
NEW_R_VALUES = [1, 5]  # 20 already computed, reused below
CLASSES = [TrafficClass.EMERGENCY, TrafficClass.TELEMETRY,
           TrafficClass.SCIENCE_BULK, TrafficClass.MEDIA]

with open("results/fig8_results.json") as f:
    old = json.load(f)

policies = old["meta"]["policies"]
locked_ttls = old["meta"]["locked_ttls"]

# restructure to be R-aware: stress_by_r[class][R][ttl][policy] = {...}
stress_by_r = {tc.value: {} for tc in CLASSES}

# reuse already-computed R=20 stress data (no recompute)
for tc in CLASSES:
    stress_by_r[tc.value]["20"] = old["stress_plan"][tc.value]

# compute the two new R values fresh
for tc in CLASSES:
    locked = locked_ttls[tc.value]
    ttl_values = [locked * m for m in MULTIPLIERS]
    for r in NEW_R_VALUES:
        t0 = time.time()
        result = sweep_ttl(tc, ttl_values, CSV_PATH, rate_multiplier=r,
                            stress=True, stress_rate_bps=10_000.0)
        stress_by_r[tc.value][str(r)] = {
            ttl: {p: {"mission_utility": result[ttl][p]["mission_utility"],
                       "own_delivery_ratio": result[ttl][p]["delivery_ratio_by_class"].get(tc.value, None)}
                  for p in policies}
            for ttl in ttl_values
        }
        print(f"{tc.value:12s} R={r:>3d} done in {time.time()-t0:.1f}s")

output = {
    "meta": {**old["meta"], "r_values": NEW_R_VALUES + [20]},
    "stress_by_r": stress_by_r,
    "real_plan": old["real_plan"],  # kept as-is, single R=20 reference
}
with open("results/fig8_results.json", "w") as f:
    json.dump(output, f, indent=2)
print("saved results/fig8_results.json (now R-aware)")
