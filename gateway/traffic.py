"""
gateway/traffic.py

Single source of truth for the LunaBridge traffic taxonomy.
The classifier, scheduler, and telemetry schema all import from here.

Sources:
  - TrafficClass names and ordering: supervisor brief S2-W5b
  - Utility weights (100/10/3/1): supervisor brief S2-W5b formula
    U = 100*E + 10*T + 3*Sm + 1*Sb - 1000*E_expired
  - Emergency TTL 300s, science_metadata TTL 24h,
    science_bulk TTL 7d, media TTL 0s: supervisor brief S2-W5b
  - BPv7 no in-band priority: RFC 9171 Section 4.1.3
  - DSCP framing: RFC 2474 (IP-layer), RFC 4594 (codepoint names)
  - TS 23.501 Table 5.7.4-1 maps 5QI to QoS characteristics only;
    no DSCP column exists (verified). No normative 5QI->DSCP table exists.

Placeholders (no external source — must update before scheduler build):
  - COMMAND_CONTROL: ttl_s, utility_weight, custody_required
  - OAM: ttl_s, utility_weight
  - TELEMETRY: ttl_s — brief says "gap + 1h"; gap from S3 contact plan (pending)
  - TELEMETRY: custody_required — judgment call, not in brief
  - queue_budget: all 1.0 (no cap); inert until WFQ scheduler (Days 16-28)
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class TrafficClass(Enum):
    EMERGENCY        = "emergency"  # safety / abort / EVA distress
    COMMAND_CONTROL  = "command"    # C&C uplink, time-critical control
    TELEMETRY        = "telemetry"  # health / housekeeping telemetry
    OAM              = "oam"        # SNPN OAM logs, KPIs, alarms
    SCIENCE_METADATA = "sci_meta"   # small index bundles; must arrive
    SCIENCE_BULK     = "sci_bulk"   # large datasets, no hard deadline
    MEDIA            = "media"      # EVA video; real-time or expendable


@dataclass(frozen=True)
class ClassSpec:
    """Per-class scheduling metadata.

    rank            : strict-priority order, 0 = highest.
    utility_weight  : contribution to U per delivered bundle (brief S2-W5b).
    default_ttl_s   : default bundle lifetime in seconds.
    custody_required: DTN custody transfer required (no silent drop).
    expendable      : safe to drop after real-time window closes.
    queue_budget    : POLICY KNOB — max fraction of contact window capacity
                      for this class. 1.0 = no cap. Inert under FIFO /
                      strict-priority. Do not treat as a justified number.
    """
    rank: int
    utility_weight: float
    default_ttl_s: float
    custody_required: bool
    expendable: bool
    queue_budget: float = 1.0


CLASS_SPECS: dict[TrafficClass, ClassSpec] = {
    # --- sourced from brief S2-W5b ---
    TrafficClass.EMERGENCY: ClassSpec(
        rank=0, utility_weight=100.0, default_ttl_s=300.0,
        custody_required=True, expendable=False,
    ),
    # --- PLACEHOLDER: ttl_s and utility_weight have no source in brief or spec.
    #     Chosen as interpolation between EMERGENCY and TELEMETRY values.
    #     custody_required=True is a judgment call; not stated in brief.
    #     Update before scheduler build (Days 16-28). ---
    TrafficClass.COMMAND_CONTROL: ClassSpec(
        rank=1, utility_weight=30.0, default_ttl_s=900.0,
        custody_required=True, expendable=False,
    ),
    # --- PLACEHOLDER: ttl_s = "gap + 1h" per brief, but gap comes from
    #     S3 contact plan (pending). Hardcoded 3600s assumes gap ~ 0.
    #     custody_required=True is a judgment call; not stated in brief.
    #     Update when S3-W3/W4 output is available. ---
    TrafficClass.TELEMETRY: ClassSpec(
        rank=2, utility_weight=10.0, default_ttl_s=3600.0,
        custody_required=True, expendable=False,
    ),
    # --- PLACEHOLDER: ttl_s and utility_weight have no source in brief or spec.
    #     Update before scheduler build (Days 16-28). ---
    TrafficClass.OAM: ClassSpec(
        rank=3, utility_weight=3.0, default_ttl_s=6*3600.0,
        custody_required=False, expendable=False,
    ),
    # --- sourced from brief S2-W5b ---
    TrafficClass.SCIENCE_METADATA: ClassSpec(
        rank=4, utility_weight=3.0, default_ttl_s=24*3600.0,
        custody_required=True, expendable=False,
    ),
    # --- sourced from brief S2-W5b ---
    TrafficClass.SCIENCE_BULK: ClassSpec(
        rank=5, utility_weight=1.0, default_ttl_s=7*86400.0,
        custody_required=False, expendable=False,
    ),
    # --- sourced from brief S2-W5b (TTL=0: real-time only) ---
    TrafficClass.MEDIA: ClassSpec(
        rank=6, utility_weight=0.0, default_ttl_s=0.0,
        custody_required=False, expendable=True,
    ),
}


def spec(tc: TrafficClass) -> ClassSpec:
    return CLASS_SPECS[tc]


def classes_by_priority() -> list[TrafficClass]:
    """Return all classes sorted highest priority first."""
    return sorted(TrafficClass, key=lambda c: CLASS_SPECS[c].rank)
