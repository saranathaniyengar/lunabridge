import json
import numpy as np
from gateway.workload_generator import (
    CLASS_PARAMS, _generate_class_arrivals, _generate_media_streams,
)
from gateway.traffic import CLASS_SPECS, TrafficClass

R_VALUES = [1, 5, 10, 15, 20, 30]
DURATIONS_H = np.arange(0, 6.25, 0.25)
BUFFER_LIMIT_BYTES = 256_000_000  # 256 MB, CM4-class
MAX_DURATION_S = 6.0 * 3600.0

CLASS_SEEDS = {
    TrafficClass.EMERGENCY: 1001,
    TrafficClass.TELEMETRY: 1002,
    TrafficClass.SCIENCE_BULK: 1003,
    TrafficClass.MEDIA: 1004,
}


def simulate_capped_buffer(r, checkpoints_s):
    """Event-driven simulation enforcing a REAL 256MB admission cap --
    matches Scheduler._try_admit's logic: purge expired bundles first
    (frees space), then admit the new arrival only if it fits; otherwise
    reject it as overflow. Walks arrivals in actual chronological order
    (not snapshot-and-filter) since admission now depends on buffer state
    at that exact moment, not just "arrived by now"."""
    all_events = []
    for tc, params in CLASS_PARAMS.items():
        rng = np.random.default_rng(CLASS_SEEDS[tc])
        scaled = {**params, "rate_per_h": params["rate_per_h"] * r}
        events = _generate_class_arrivals(rng, tc, scaled, MAX_DURATION_S)
        ttl = CLASS_SPECS[tc].default_ttl_s
        for t, _, size in events:
            all_events.append((t, size, t + ttl))

    # MEDIA is a continuous CBR video source (not Poisson), so it is generated
    # separately -- and it now dominates buffer fill during a blackout.
    media_rng = np.random.default_rng(CLASS_SEEDS[TrafficClass.MEDIA])
    media_ttl = CLASS_SPECS[TrafficClass.MEDIA].default_ttl_s
    for t, _, size in _generate_media_streams(media_rng, MAX_DURATION_S, r):
        all_events.append((t, size, t + media_ttl))

    all_events.sort(key=lambda e: e[0])

    active = []
    occupied = 0
    overflowed_bytes = 0

    occ_series, overflow_series = [], []
    checkpoints = list(checkpoints_s)
    ci = 0

    def purge_expired(now):
        nonlocal occupied, active
        still = []
        for size, exp in active:
            if exp > now:
                still.append((size, exp))
            else:
                occupied -= size
        active = still

    for (t, size, exp) in all_events:
        while ci < len(checkpoints) and checkpoints[ci] < t:
            purge_expired(checkpoints[ci])
            occ_series.append(occupied / 1_000_000)
            overflow_series.append(overflowed_bytes / 1_000_000)
            ci += 1

        purge_expired(t)
        if occupied + size <= BUFFER_LIMIT_BYTES:
            active.append((size, exp))
            occupied += size
        else:
            overflowed_bytes += size

    while ci < len(checkpoints):
        purge_expired(checkpoints[ci])
        occ_series.append(occupied / 1_000_000)
        overflow_series.append(overflowed_bytes / 1_000_000)
        ci += 1

    return occ_series, overflow_series


results = {"meta": {"r_values": R_VALUES, "durations_h": DURATIONS_H.tolist(),
                     "buffer_limit_mb": 256}, "occupancy_mb": {}, "overflow_mb": {}}

checkpoints_s = DURATIONS_H * 3600.0
for r in R_VALUES:
    occ, overflow = simulate_capped_buffer(r, checkpoints_s)
    results["occupancy_mb"][str(r)] = occ
    results["overflow_mb"][str(r)] = overflow
    print(f"R={r}: max occupancy={max(occ):.1f} MB, total overflow={overflow[-1]:.1f} MB")

with open("results/fig7_results.json", "w") as f:
    json.dump(results, f, indent=2)
print("saved results/fig7_results.json")
