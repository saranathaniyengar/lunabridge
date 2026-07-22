"""
gateway/workload_generator.py

Synthetic BundleRecord workload generator for the 7-policy scheduler
comparison (S2-W6-7). Produces traffic for a ~20-device surface outpost
across the full 90-day LCRNS contact-plan span, for all four traffic classes
(EMERGENCY, TELEMETRY, SCIENCE_BULK, MEDIA).

Traffic model (revised so offered load matches the Paper 4 traffic model)
-------------------------------------------------------------------------
The paper's rate-mismatch result needs the island to offer ~9-13 Mbps so that
R = lambda_ingress / C_backhaul,eff can exceed 1 against the real ~8 Mbps
effective backhaul (10 Mbps link x eta_contact). The paper attributes that
demand to "one or two 1080p feeds [that] dominate".

Earlier this module modeled every class -- MEDIA included -- as Poisson
arrivals of small discrete bundles. That put MEDIA at ~0.24 bps (55 KB every
30 min) and the whole workload at ~13.9 kbps: ~700x below the paper's demand,
and dominated by SCIENCE_BULK, not video. R>1 could then only be reached by
throttling the backhaul to 10 kbps in sweep_harness (the "stress plan"), which
contradicts the paper. See git history / the review notes.

Fix: model MEDIA as it is described -- continuous constant-bit-rate 1080p video
feeds -- while EMERGENCY / TELEMETRY / SCIENCE_BULK stay Poisson (they are
genuinely bursty and small). With MEDIA_STREAMS=2 at 6 Mbps each the aggregate
offered load is ~12 Mbps, so against the real 10 Mbps link (eta_contact~0.82,
C_eff~8.2 Mbps) R ~= 1.4 emerges NATURALLY -- no stress plan needed. One feed
gives ~6 Mbps (R just below 1), two give ~12 Mbps (R~1.5), matching the paper's
"one or two feeds ... 9-13 Mbps". Run the sweep on the REAL contact plan now.

Design notes
------------
  - EMERGENCY / TELEMETRY / SCIENCE_BULK: Poisson arrivals (exponential
    inter-arrival), only rate and size-range differ per class. SCIENCE_BULK
    size is log-uniform over 1-50 MB so "10x bigger" is equally likely across
    the range (uniform would bunch draws near 50 MB).
  - MEDIA: MEDIA_STREAMS concurrent CBR feeds, each emitting one segment every
    MEDIA_SEGMENT_S seconds of size bitrate*segment/8 bytes. Streams are phase-
    staggered so their segment boundaries do not arrive in lockstep. This is
    the only continuous multi-Mbps source; it sets the aggregate offered load.
  - ingress_ts uses the SAME clock convention as the real contact plan CSV
    (lcrns_relay_contact_plan_1sv.csv): seconds since the plan's own t=0 epoch.
  - One shared rng, one fixed seed across all classes -- this is what makes
    "same workload, all 7 policies" a fair comparison.
  - rate_multiplier (R sweep, Fig 3): scales BOTH the Poisson rates and the
    MEDIA aggregate bitrate. Because the baseline now offers ~C_backhaul-worth
    of traffic, rate_multiplier is a meaningful multiple of the natural R~1
    operating point, not a multiplier on an irrelevant 13.9 kbps baseline.

TTLs are set elsewhere (gateway/traffic.py CLASS_SPECS); rec.set_ttl() pulls
TTL from CLASS_SPECS. MEDIA default_ttl_s is 3600 s (a stale video segment is
worthless), which is the correct semantics for CBR segments.
"""

import itertools
from typing import List, Tuple

import numpy as np

from gateway.traffic import TrafficClass
from gateway.telemetry import BundleRecord

SPAN_HOURS = 90 * 24
SPAN_SECONDS = SPAN_HOURS * 3600.0

# --- Bursty classes: Poisson arrivals of small discrete bundles -------------
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
}

# --- MEDIA: continuous CBR 1080p video feeds (dominates offered load) -------
# "one or two 1080p feeds dominate" -> 2 x 6 Mbps ~= 12 Mbps aggregate.
MEDIA_STREAMS = 2                 # concurrent feeds (set 1 for the R<1 case)
MEDIA_BITRATE_BPS = 6_000_000     # ~6 Mbps per 1080p H.265 feed
MEDIA_SEGMENT_S = 60.0            # one bundle per this many seconds of video
MEDIA_JITTER = 0.10               # +/-10% VBR size jitter around the CBR mean


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


def _generate_media_streams(
    rng: np.random.Generator,
    span_seconds: float,
    rate_multiplier: float,
) -> List[Tuple[float, TrafficClass, int]]:
    """MEDIA_STREAMS continuous CBR feeds, phase-staggered, with light VBR
    jitter. Aggregate mean bitrate = MEDIA_STREAMS * MEDIA_BITRATE_BPS * mult."""
    events: List[Tuple[float, TrafficClass, int]] = []
    bitrate = MEDIA_BITRATE_BPS * rate_multiplier
    mean_seg_bytes = bitrate * MEDIA_SEGMENT_S / 8.0
    for s in range(MEDIA_STREAMS):
        phase = rng.uniform(0.0, MEDIA_SEGMENT_S)   # de-synchronise feeds
        t = phase
        while t < span_seconds:
            jitter = 1.0 + rng.uniform(-MEDIA_JITTER, MEDIA_JITTER)
            size = int(round(mean_seg_bytes * jitter))
            events.append((t, TrafficClass.MEDIA, max(size, 1)))
            t += MEDIA_SEGMENT_S
    return events


def generate_workload(seed: int = 42, span_seconds: float = SPAN_SECONDS,
                       rate_multiplier: float = 1.0) -> List[BundleRecord]:
    """Generate the full synthetic workload: the three Poisson classes plus the
    continuous MEDIA feeds, merged into one time-ordered stream of BundleRecords
    with TTL already set.

    rate_multiplier scales both the Poisson rates and the MEDIA aggregate
    bitrate. 1.0 = baseline (~12 Mbps offered, R~1.4 on the real 10 Mbps link)."""
    rng = np.random.default_rng(seed)
    bundle_seq = itertools.count(1)

    scaled_params = {
        tc: {**params, "rate_per_h": params["rate_per_h"] * rate_multiplier}
        for tc, params in CLASS_PARAMS.items()
    }

    all_events: List[Tuple[float, TrafficClass, int]] = []
    for tc, params in scaled_params.items():
        all_events.extend(_generate_class_arrivals(rng, tc, params, span_seconds))
    all_events.extend(_generate_media_streams(rng, span_seconds, rate_multiplier))

    all_events.sort(key=lambda e: e[0])

    records = []
    for t, tc, size in all_events:
        bundle_id = str(next(bundle_seq))
        rec = BundleRecord(
            bundle_id=bundle_id,
            flow_id=f"synthetic-{tc.value}-{bundle_id}",
            traffic_class=tc,
            size_bytes=size,
            ingress_ts=t,
        )
        rec.set_ttl()
        records.append(rec)

    return records


def expected_offered_load_mbps(rate_multiplier: float = 1.0) -> float:
    """Analytic mean offered load (Mbps) for the current parameters, so the
    baseline can be asserted in tests / CI without generating the full stream."""
    load_bps = MEDIA_STREAMS * MEDIA_BITRATE_BPS * rate_multiplier
    for params in CLASS_PARAMS.values():
        lo, hi = params["size_range"]
        if params["log_uniform"]:
            mean_bytes = (hi - lo) / np.log(hi / lo)   # log-uniform mean
        else:
            mean_bytes = 0.5 * (lo + hi)
        load_bps += params["rate_per_h"] * rate_multiplier * mean_bytes * 8.0 / 3600.0
    return load_bps / 1e6


if __name__ == "__main__":
    print(f"Expected mean offered load @ mult=1.0: "
          f"{expected_offered_load_mbps():.2f} Mbps "
          f"({MEDIA_STREAMS} x {MEDIA_BITRATE_BPS/1e6:.0f} Mbps MEDIA feeds)\n")

    records = generate_workload(seed=42)
    total_bytes = sum(r.size_bytes for r in records)
    measured_mbps = total_bytes * 8.0 / SPAN_SECONDS / 1e6
    print(f"Generated {len(records):,} bundles across {SPAN_HOURS:.0f}h "
          f"({SPAN_HOURS/24:.0f} days); measured mean load {measured_mbps:.2f} Mbps\n")
    for tc in list(CLASS_PARAMS) + [TrafficClass.MEDIA]:
        cr = [r for r in records if r.traffic_class == tc]
        sizes = [r.size_bytes for r in cr]
        share = sum(sizes) * 8.0 / SPAN_SECONDS / 1e6
        print(f"  {tc.value:15s}  count={len(cr):8,}  "
              f"load={share:7.3f} Mbps  "
              f"size min/mean/max = {min(sizes):>12,} / {int(np.mean(sizes)):>12,} / {max(sizes):>12,} B")
