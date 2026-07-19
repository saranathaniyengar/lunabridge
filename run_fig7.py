import json
import numpy as np
from gateway.workload_generator import CLASS_PARAMS, _generate_class_arrivals
from gateway.traffic import CLASS_SPECS

# Buffer occupancy at the END of a blackout of duration D: sum of bytes
# for every bundle that (a) arrived by time D and (b) has NOT yet expired
# (expiration_ts > D). No scheduler/window logic needed -- during a
# blackout nothing drains regardless of policy, so occupancy is purely
# arrivals-minus-expiry, computed directly from the same locked rates/
# sizes/TTLs the rest of the project already uses.

R_VALUES = [1.5, 2.0]
DURATIONS_H = np.arange(0, 6.25, 0.25)  # 0 to 6h, every 15 min
BUFFER_LIMIT_MB = 256  # CM4-class

results = {"meta": {"r_values": R_VALUES, "durations_h": DURATIONS_H.tolist(),
                     "buffer_limit_mb": BUFFER_LIMIT_MB}, "occupancy_mb": {}}

for r in R_VALUES:
    occ_series = []
    for d_h in DURATIONS_H:
        d_s = d_h * 3600.0
        rng = np.random.default_rng(42)  # same fixed seed as every other figure
        total_bytes = 0
        for tc, params in CLASS_PARAMS.items():
            scaled = {**params, "rate_per_h": params["rate_per_h"] * r}
            events = _generate_class_arrivals(rng, tc, scaled, d_s)
            ttl = CLASS_SPECS[tc].default_ttl_s
            for t, _, size in events:
                if t + ttl > d_s:  # still alive at the moment blackout ends
                    total_bytes += size
        occ_series.append(total_bytes / 1_000_000)  # MB
    results["occupancy_mb"][str(r)] = occ_series
    print(f"R={r}: done, max occupancy = {max(occ_series):.1f} MB")

with open("results/fig7_results.json", "w") as f:
    json.dump(results, f, indent=2)
print("saved results/fig7_results.json")
