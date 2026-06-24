"""
gateway/priority_classifier.py

Maps an IP DSCP value (0-63) to a LunaBridge TrafficClass.

Sources:
  - DSCP codepoint values and names: RFC 2474, RFC 4594
  - RFC 4594 service class table (EF=Telephony, AF41=Multimedia
    Conferencing, CS2=OAM, CS1=Low-Priority Data, CS0=Standard)
  - EF/46 relabeled EMERGENCY: local gateway policy, not RFC 4594
  - AF3x relabeled TELEMETRY: local gateway policy
  - AF2x relabeled SCIENCE_METADATA: local gateway policy

Not a standard:
  - There is no normative 3GPP 5QI->DSCP table.
    TS 23.501 Table 5.7.4-1 maps 5QI to QoS characteristics
    (Priority Level, PDB, PER) only. No DSCP column exists (verified).
  - The only 5QI<->DSCP mapping on record is a non-normative IETF
    example draft (draft-cbs-teas-5qi-to-dscp-mapping).

Priority enforcement:
  - BPv7 (RFC 9171) has no in-band priority field.
  - Classification here is internal bookkeeping only.
  - Priority is enforced by the gateway queue, never stamped in the bundle.
"""

from __future__ import annotations
from .traffic import TrafficClass, spec

# Authoritative lookup table. Any codepoint not listed defaults to
# best-effort (DEFAULT_CLASS). Unknown traffic is never promoted --
# a UE cannot acquire elevated priority with an unrecognised DSCP.
DSCP_TO_CLASS: dict[int, TrafficClass] = {
    46: TrafficClass.EMERGENCY,         # EF  (RFC 4594: Telephony — policy relabel)
    56: TrafficClass.COMMAND_CONTROL,   # CS7
    48: TrafficClass.COMMAND_CONTROL,   # CS6 (RFC 4594: Network Control)
    40: TrafficClass.COMMAND_CONTROL,   # CS5 (RFC 4594: Signaling)
    34: TrafficClass.MEDIA,             # AF41 (RFC 4594: Multimedia Conferencing)
    36: TrafficClass.MEDIA,             # AF42
    38: TrafficClass.MEDIA,             # AF43
    26: TrafficClass.TELEMETRY,         # AF31 (policy relabel)
    28: TrafficClass.TELEMETRY,         # AF32
    30: TrafficClass.TELEMETRY,         # AF33
    18: TrafficClass.SCIENCE_METADATA,  # AF21 (policy relabel)
    20: TrafficClass.SCIENCE_METADATA,  # AF22
    22: TrafficClass.SCIENCE_METADATA,  # AF23
    10: TrafficClass.SCIENCE_BULK,      # AF11
    12: TrafficClass.SCIENCE_BULK,      # AF12
    14: TrafficClass.SCIENCE_BULK,      # AF13
    16: TrafficClass.OAM,               # CS2  (RFC 4594: OAM)
     8: TrafficClass.SCIENCE_BULK,      # CS1  (RFC 4594: Low-Priority Data)
     0: TrafficClass.SCIENCE_BULK,      # CS0  (RFC 4594: Standard / best-effort)
}

DEFAULT_CLASS = TrafficClass.SCIENCE_BULK


def classify_packet(dscp: int) -> TrafficClass:
    """Return the TrafficClass for a given DSCP value (0-63).

    Explicit table only. Unknown codepoints return DEFAULT_CLASS (best-effort).
    Raises ValueError if dscp is outside the valid 0-63 range.
    """
    if not 0 <= dscp <= 63:
        raise ValueError(f"DSCP out of range 0-63: {dscp}")
    return DSCP_TO_CLASS.get(dscp, DEFAULT_CLASS)


if __name__ == "__main__":
    # Day 7 acceptance test cases.
    cases = [
        (46, "EF   -> emergency  (policy relabel from RFC 4594 Telephony)"),
        ( 0, "CS0  -> sci_bulk   (best-effort default)"),
        (34, "AF41 -> media      (RFC 4594 Multimedia Conferencing)"),
        ( 8, "CS1  -> sci_bulk   (RFC 4594 Low-Priority Data)"),
        (37, "37   -> sci_bulk   (unknown codepoint, NOT promoted)"),
    ]
    print(f"{'dscp':>4}  {'class':<12}  {'rank'}  {'ttl_s':>8}  {'custody':<7}  note")
    print("-" * 75)
    for dscp, note in cases:
        tc = classify_packet(dscp)
        s = spec(tc)
        print(f"{dscp:>4}  {tc.value:<12}  {s.rank}     "
              f"{s.default_ttl_s:>8.0f}  {str(s.custody_required):<7}  {note}")
