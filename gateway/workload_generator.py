"""
gateway/workload_generator.py

Synthetic BundleRecord workload generator for the 6-policy scheduler
comparison (S2-W6-7). Produces Poisson-arrival traffic for a ~20-device
surface outpost across the full 90-day LCRNS contact-plan span, for all
four traffic classes (EMERGENCY, TELEMETRY, SCIENCE_BULK, MEDIA).

Design notes (see conversation log for full derivation):
  - All four classes use the SAME generator mechanism (Poisson arrivals
    i.e. exponential inter-arrival times) -- only rate and size-range
    differ per class. MEDIA is deliberately NOT EVA-gated: "media could
    be anything" (lander video, astronaut photos, at any time) is a
    claim we can't defend restricting to EVA windows only, so MEDIA is
    constant-rate like the other three.
  - SCIENCE_BULK size is drawn log-uniform, not uniform: 1-50MB is a
    ~50x spread, and uniform sampling would bunch draws near the high
    end. Log-uniform makes "10x bigger" equally likely across the range.
  - ingress_ts uses the SAME clock convention as the real contact plan
    CSV (lcrns_relay_contact_plan_1sv.csv): seconds since the plan's own
    t=0 epoch, NOT time.time()-style Unix seconds. Confirmed against
    the CSV: row 0's gap_before_s (7839.9) matches its start_sec (7839)
    almost exactly, i.e. start_sec is time-since-plan-start, not epoch
    time (interpreting 7839 as Unix time would land in 1970, which
    contradicts the row's own start_utc of 2027-03-01).
  - One shared rng, one fixed seed, reused across all four classes'
    draws -- this is what makes "same workload, all 6 policies" a fair
    comparison rather than each policy facing different random luck.

Rates (per original S2-W6-7 ticket):
    EMERGENCY     ~1 per 10 h
    TELEMETRY     ~5 per hour
    SCIENCE_BULK  ~1 per 2 h
    MEDIA         ~1 per 30 min (= 2 per hour)

Size ranges (per original S2-W6-7 ticket, locked as-is, not re-derived):
    EMERGENCY     64-768 bytes        (uniform)
    TELEMETRY     1-8 KB              (uniform)
    SCIENCE_BULK  1-50 MB             (log-uniform)
    MEDIA         10-100 KB           (uniform)

TTLs and link rate are set elsewhere (gateway/traffic.py CLASS_SPECS,
lcrns_relay_contact_plan_1sv.csv rate_bps) -- this module only produces
arrival timing and size; rec.set_ttl() pulls TTL from CLASS_SPECS.
"""

import itertools
from typing import List, Tuple

import numpy as np

from gateway.traffic import TrafficClass
from gateway.telemetry import BundleRecord

# 90-day plan span, matching lcrns_relay_contact_plan_1sv.csv's own t=0..~2160h scale
SPAN_HOURS = 90 * 24
SPAN_SECONDS = SPAN_HOURS * 3600.0

# rate_per_h = arrivals per hour; size_range in bytes; log_uniform = sampling mode
CLASS_PARAMS = {
    TrafficClass.EMERGENCY: dict(
        rate_per_h=1 / 10,
        size_range=(64, 768),
        log_uniform=False,
    ),
    TrafficClass.TELEMETRY: dict(
        rate_per_h=5,
        size_range=(1_000, 8_000),
        log_uniform=False,
    ),
    TrafficClass.SCIENCE_BULK: dict(
        rate_per_h=1 / 2,
        size_range=(1_000_000, 50_000_000),
        log_uniform=True,
    ),
    TrafficClass.MEDIA: dict(
        rate_per_h=1 / 0.5,  # 1 per 30 min = 2 per hour
        size_range=(10_000, 100_000),
        log_uniform=False,
    ),
}


def _sample_size(rng: np.random.Generator, size_range: Tuple[float, float], log_uniform: bool) -> int:
    lo, hi = size_range
    if log_uniform:
        log_lo, log_hi = np.log10(lo), np.log10(hi)
        return int(round(10 ** rng.uniform(log_lo, log_hi)))
    return int(round(rng.uniform(lo, hi)))


def _generate_class_arrivals(
    rng: np.random.Generator,
    tc: TrafficClass,
    params: dict,
    span_seconds: float,
) -> List[Tuple[float, TrafficClass, int]]:
    """Poisson arrivals (exponential inter-arrival times) for one class
    across the full span. Returns list of (ingress_ts, traffic_class, size_bytes)."""
    mean_gap_s = 3600.0 / params["rate_per_h"]
    events = []
    t = 0.0
    while True:
        gap = rng.exponential(scale=mean_gap_s)
        t += gap
        if t >= span_seconds:
            break
        size = _sample_size(rng, params["size_range"], params["log_uniform"])
        events.append((t, tc, size))
    return events


def generate_workload(seed: int = 42, span_seconds: float = SPAN_SECONDS) -> List[BundleRecord]:
    """Generate the full synthetic workload: all four classes, merged into
    one time-ordered stream (traffic arrives interleaved at the gateway,
    not grouped by class), as a list of BundleRecords with TTL already set."""
    rng = np.random.default_rng(seed)
    bundle_seq = itertools.count(1)

    all_events: List[Tuple[float, TrafficClass, int]] = []
    for tc, params in CLASS_PARAMS.items():
        all_events.extend(_generate_class_arrivals(rng, tc, params, span_seconds))

    all_events.sort(key=lambda e: e[0])

    records = []
    for t, tc, size in all_events:
        bundle_id = str(next(bundle_seq))
        rec = BundleRecord(
            bundle_id=bundle_id,
            flow_id=f"synthetic-{tc.value}-{bundle_id}",
            traffic_class=tc,
            size_bytes=size,
            ingress_ts=t,  # seconds since plan epoch -- matches contact plan CSV convention
        )
        rec.set_ttl()
        records.append(rec)

    return records


if __name__ == "__main__":
    records = generate_workload(seed=42)

    print(f"Generated {len(records)} bundles across {SPAN_HOURS:.0f}h ({SPAN_HOURS/24:.0f} days)\n")

    for tc in CLASS_PARAMS:
        class_records = [r for r in records if r.traffic_class == tc]
        sizes = [r.size_bytes for r in class_records]
        expected_count = SPAN_HOURS * CLASS_PARAMS[tc]["rate_per_h"]
        print(f"  {tc.value:15s}  count={len(class_records):6d}  "
              f"(expected~{expected_count:.0f})  "
              f"size min/mean/max = {min(sizes):>10,} / {int(np.mean(sizes)):>10,} / {max(sizes):>10,} bytes")
