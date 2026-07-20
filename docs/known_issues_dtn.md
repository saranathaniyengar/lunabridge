# Known Issue: N6-to-DTN full pipeline wiring (S2-W6-7, DTN integration)

## Status
DTN core transport is fully proven, independently verified multiple times:
- µD3TN built from source (moon + earth nodes), confirmed running
- Real routing established between two separate containers (moon <-> earth)
- Real BPv7 bundles sent/received via µD3TN's own aap2-ping echo test,
  consistent RTT (~2.5ms baseline, confirmed)
- Real Earth-Moon light-time delay (1.28s one-way) successfully applied
  via tc netem, surgically isolated to only Earth-bound traffic (verified
  via u32 filter match + confirmed the live 5G stack was unaffected)
- Real 5G stack (Open5GS + srsRAN gNB/UE) confirmed live, real PDU session
- N6 interception confirmed correctly classifying real UE traffic
  (DSCP 184 -> codepoint 46 -> EMERGENCY, ttl=300s, rank=0)
- bundle_sender.py confirmed correctly connecting to and sending via
  µD3TN's real AAP2 API (multiple real bugs found+fixed: CLA name mismatch,
  adu_flags list-wrapping, ud3tn-utils version mismatch causing a
  BundleADU schema mismatch)

## Open issue
Wiring N6Interceptor's live callback directly to BundleSender.send() (so
real intercepted UE packets become real bundles sent end-to-end to the
earth node) does not yet reliably deliver to the earth-side receiver
(aap2-receive), despite:
- Sender reporting successful send (no exception, correct log line)
- Moon's real routing/CLA link to earth confirmed alive throughout
- __pycache__ cleared and python3 -B (no-cache) used to rule out stale
  bytecode as the cause

Moon's daemon log shows a recurring warning at send time:
  "AAP2Agent: User-defined creation timestamps are unsupported!"
despite creation_timestamp_ms being removed from bundle_sender.py's
BundleADU construction. Root cause not yet isolated -- possibly:
- ud3tn-utils library internally defaulting/re-adding this field
  regardless of caller (unconfirmed, needs source-level check)
- A second, unremoved reference to creation_timestamp_ms elsewhere in
  the call path (also unconfirmed)

## Next steps (not done tonight, flagged for follow-up)
1. Check ud3tn_utils/aap2/aap2_client.py's send_adu()/BundleADU
   construction path directly for where this field might be re-added
2. Consider testing with -v (verbose) on the earth-side aap2-receive
   process specifically to see if bundles arrive but are silently
   rejected/misrouted, vs. never arriving at all
3. This does NOT block the core DTN integration claim already proven
   above -- only the specific "real live UE packet -> real bundle ->
   confirmed at earth" full-chain smoke test remains open
