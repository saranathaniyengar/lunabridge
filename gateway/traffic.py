"""
gateway/traffic.py

Single source of truth for the LunaBridge traffic taxonomy.
The classifier, scheduler, and telemetry schema all import from here.

Sources:
  - TrafficClass names and ordering: supervisor brief S2-W5b
  - Utility weights: supervisor brief S2-W5b formula, ORIGINALLY
    U = 100*E + 10*T + 3*Sm + 1*Sb - 1000*E_expired
    REDUCED Day 11.5 (SCIENCE_METADATA removed, see below): Sm term is
    permanently zero. Effective formula now: U = 100*E + 10*T + 1*Sb - 1000*E_expired
  - Emergency TTL, science_bulk TTL, media TTL: ORIGINALLY supervisor brief
    S2-W5b (300s/7d/0s respectively). OVERWRITTEN Day 11.5 per guide's
    explicit instruction with LCRNS-derived contingency values (S3 1-SV
    degraded contact plan, GMAT-derived, max_gap=5.56h).
  - BPv7 no in-band priority: RFC 9171 Section 4.3.1
  - DSCP framing: RFC 2474 (IP-layer), RFC 4594 (codepoint names)
  - TS 23.501 Table 5.7.4-1 maps 5QI to QoS characteristics only;
    no DSCP column exists (verified). No normative 5QI->DSCP table exists.
  - queue_budget (Day 14): WFQ scheduling weight, NOT a byte cap and NOT
    reused from utility_weight. TELEMETRY=10.0 sourced from utility_weight
    (unchanged coincidence, both from brief). SCIENCE_BULK=1.0 and
    MEDIA=1.0 are an EXPLICIT POLICY FLOOR, not derived from any source --
    reusing MEDIA's utility_weight=0.0 directly would zero its WFQ share
    entirely, which Day 14's session explicitly decided against. See
    SESSION_STATE.md Day 14 Decisions 2-3. EMERGENCY's queue_budget is
    left at the dataclass default (1.0) but is INERT -- Decision 1
    excludes EMERGENCY from WFQ entirely (hard-preempt, always).

REMOVED Day 11.5 (deliberate deletion, not a data finding):
  - COMMAND_CONTROL: never had a source (ttl_s, utility_weight,
    custody_required all placeholder since Day 7/8).
  - OAM: never had a source (ttl_s, utility_weight placeholder since Day 7/8).
  - SCIENCE_METADATA: WAS brief-sourced (24h TTL, weight=3, the "Sm" term
    above) -- removed anyway per explicit instruction, not because it was
    unsourced. Permanently zeroes the Sm term in the formula above.
  DSCP codepoints and test references previously pointing at these three
  are remapped -- see priority_classifier.py and telemetry.py.

Still open:
  - TELEMETRY: custody_required — judgment call, not in brief. Untouched.

RESOLVED Day 14:
  - queue_budget: TELEMETRY:SCIENCE_BULK:MEDIA = 10:1:1. No longer
    inert -- read by gateway/scheduler.py's WFQ/WFQ_SKIP_OVER policies.
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class TrafficClass(Enum):
    """Day 11.5: reduced from 7 to 4 classes. COMMAND_CONTROL, OAM,
    SCIENCE_METADATA removed -- see module docstring."""
    EMERGENCY    = "emergency"  # safety / abort / EVA distress
    TELEMETRY    = "telemetry"  # health / housekeeping telemetry
    SCIENCE_BULK = "sci_bulk"   # large datasets, no hard deadline
    MEDIA        = "media"      # EVA video; real-time or expendable


@dataclass(frozen=True)
class ClassSpec:
    """Per-class scheduling metadata.

    rank            : strict-priority order, 0 = highest.
    utility_weight  : contribution to U per delivered bundle (brief S2-W5b).
    default_ttl_s   : default bundle lifetime in seconds.
    custody_required: DTN custody transfer required (no silent drop).
    expendable      : safe to drop after real-time window closes.
    queue_budget    : Day 14 -- WFQ scheduling weight (fraction/ratio of
                      the post-EMERGENCY remainder, NOT a byte cap). See
                      module docstring "RESOLVED Day 14" for sourcing.
                      INERT for EMERGENCY (Decision 1 excludes it from WFQ).
    """
    rank: int
    utility_weight: float
    default_ttl_s: float
    custody_required: bool
    expendable: bool
    queue_budget: float = 1.0


# --- OVERWRITTEN (S2-W6-7 TTL redesign): all four default_ttl_s
#     values below were exact multiples of one contact-gap-derived
#     number (Day 11.5) -- flagged as trivially tied to the plan's
#     own gap structure, making TTL_EXPIRED near-impossible against
#     the real 90-day plan. Redesigned per RFC 9171 Sec 4.3.1's own
#     lifetime semantics (payload usefulness, not link characteristics).
#     EMERGENCY=300s is a stated test value (order-of-minutes,
#     time-critical), not cited to a specific mission standard.
#     TELEMETRY/MEDIA=3600s (sampling cadence / general freshness).
#     SCIENCE_BULK=57600s (buffer-depth x arrival-rate). See
#     conversation log for full derivation. ---
CLASS_SPECS: dict[TrafficClass, ClassSpec] = {
    # --- OVERWRITTEN (Day 11.5): ttl_s was 300.0 (brief S2-W5b). Guide
    #     instructed replacing with LCRNS-derived contingency value: S3 1-SV
    #     degraded contact plan (GMAT-derived), max_gap=5.56h + 1h margin
    #     = 23627s. utility_weight=100.0 still from brief, untouched.
    #     queue_budget left at default (1.0) -- INERT for EMERGENCY. Day 14
    #     Decision 1 excludes EMERGENCY from WFQ entirely; it always
    #     hard-preempts regardless of this value. Not read by _drain_wfq
    #     for this class. ---
    TrafficClass.EMERGENCY: ClassSpec(
        rank=0, utility_weight=100.0, default_ttl_s=300.0,
        custody_required=True, expendable=False,
    ),
    # --- RESOLVED (Day 11.5): ttl_s = guide-provided contingency value, S3
    #     1-SV degraded contact plan (lcrns_relay_contact_plan_1sv.csv,
    #     GMAT-derived). Guide's sheet scales TELEMETRY at 2x the base
    #     max_gap+1h figure (23627s x2 = 47254s), not an independent
    #     gap-based derivation. Deliberately uses the degraded/blackout
    #     case, not the nominal 5-satellite case (true gap=0h there).
    # --- PLACEHOLDER: custody_required=True is still a judgment call; not
    #     stated in brief. Update when S3-W3/W4 output is available.
    # --- RESOLVED (Day 14): queue_budget=10.0 -- WFQ scheduling weight,
    #     ratio TELEMETRY:SCIENCE_BULK:MEDIA = 10:1:1. Sourced directly
    #     from utility_weight (10.0, unchanged). See SESSION_STATE.md
    #     Day 14 Decision 3. ---
    TrafficClass.TELEMETRY: ClassSpec(
        rank=1, utility_weight=10.0, default_ttl_s=3600.0,
        custody_required=True, expendable=False, queue_budget=10.0,
    ),
    # --- OVERWRITTEN (Day 11.5): ttl_s was 7*86400.0=604800.0 (brief S2-W5b).
    #     Guide instructed replacing with LCRNS-derived contingency value: S3
    #     1-SV degraded contact plan (GMAT-derived), guide's sheet scales
    #     SCIENCE at ~7x the base max_gap+1h figure (23627s x7 ~ 165389s).
    #     utility_weight=1.0 still from brief, untouched.
    # --- RESOLVED (Day 14): queue_budget=1.0 -- WFQ scheduling weight.
    #     Part of the explicit 10:1:1 policy floor (SCIENCE_BULK:MEDIA =
    #     1:1, NOT derived from utility_weight). See SESSION_STATE.md
    #     Day 14 Decision 3. ---
    TrafficClass.SCIENCE_BULK: ClassSpec(
        rank=2, utility_weight=1.0, default_ttl_s=57600.0,
        custody_required=False, expendable=False, queue_budget=1.0,
    ),
    # --- OVERWRITTEN (Day 11.5): ttl_s was 0.0 (brief S2-W5b, TTL=0:
    #     real-time only). Guide instructed replacing with LCRNS-derived
    #     contingency value: S3 1-SV degraded contact plan (GMAT-derived),
    #     guide's sheet scales MEDIA at ~0.5x the base max_gap+1h figure
    #     (23627s x0.5 ~ 11813s). NOTE: this changes MEDIA's semantics from
    #     "expire immediately if not delivered live" to "survive ~3.3h" -
    #     flagged as a real behavior change, not just a number swap.
    #     utility_weight=0.0 still from brief, untouched.
    # --- RESOLVED (Day 14): queue_budget=1.0 -- WFQ scheduling weight.
    #     Deliberately NOT reused from utility_weight=0.0, which would zero
    #     MEDIA's WFQ share entirely. Explicit 1:1 policy floor with
    #     SCIENCE_BULK, decided this session. See SESSION_STATE.md Day 14
    #     Decision 3. ---
    TrafficClass.MEDIA: ClassSpec(
        rank=3, utility_weight=0.0, default_ttl_s=3600.0,
        custody_required=False, expendable=True, queue_budget=1.0,
    ),
}


def spec(tc: TrafficClass) -> ClassSpec:
    return CLASS_SPECS[tc]


def classes_by_priority() -> list[TrafficClass]:
    """Return all classes sorted highest priority first."""
    return sorted(TrafficClass, key=lambda c: CLASS_SPECS[c].rank)
