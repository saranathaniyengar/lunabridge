import json
import numpy as np
from gateway.workload_generator import CLASS_PARAMS, _generate_class_arrivals
from gateway.traffic import CLASS_SPECS, TrafficClass

R_VALUES = [1.5, 2.0]
DURATIONS_H = np.arange(0, 6.25, 0.25)
BUFFER_LIMIT_MB = 256
MAX_DURATION_S = 6.0 * 3600.0

# Separate seed PER CLASS so one class's arrival count can never shift
# another's draws -- fixes the earlier bug where a single shared rng,
# reset per duration point, made every duration an unrelated resample
# instead of a smooth extension of the same timeline.
CLASS_SEEDS = {
    TrafficClass.EMERGENCY: 1001,
    TrafficClass.TELEMETRY: 1002,
    TrafficClass.SCIENCE_BULK: 1003,
    TrafficClass.MEDIA: 1004,
}

results = {"meta": {"r_values": R_VALUES, "durations_h": DURATIONS_H.tolist(),
                     "buffer_limit_mb": BUFFER_LIMIT_MB}, "occupancy_mb": {}}

for r in R_VALUES:
    # Generate each class's FULL 6h arrival sequence once, independently.
    all_events = []
    for tc, params in CLASS_PARAMS.items():
        rng = np.random.default_rng(CLASS_SEEDS[tc])
        scaled = {**params, "rate_per_h": params["rate_per_h"] * r}
        events = _generate_class_arrivals(rng, tc, scaled, MAX_DURATION_S)
        ttl = CLASS_SPECS[tc].default_ttl_s
        all_events.extend((t, size, ttl) for t, _, size in events)

    occ_series = []
    for d_h in DURATIONS_H:
        d_s = d_h * 3600.0
        # Same fixed set of arrivals every time -- just filter to what's
        # arrived by now and not yet expired, per duration checkpoint.
        total_bytes = sum(size for (t, size, ttl) in all_events
                           if t <= d_s and t + ttl > d_s)
        occ_series.append(total_bytes / 1_000_000)
    results["occupancy_mb"][str(r)] = occ_series
    print(f"R={r}: done, max occupancy = {max(occ_series):.1f} MB")

with open("results/fig7_results.json", "w") as f:
    json.dump(results, f, indent=2)
print("saved results/fig7_results.json")
