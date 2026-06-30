"""
gateway/n6_interceptor.py
Day 7: NFQUEUE packet capture with DSCP -> TrafficClass classification.
Day 9: telemetry wiring — per-packet BundleRecord persisted to bundles.jsonl.

Changes from Day 6:
  - __main__ block now passes a callback that resolves and logs the TrafficClass
  - No other changes to the class itself
  - Always-ACCEPT-in-finally guarantee unchanged

Day 9 note: telemetry is OBSERVATIONAL. The BundleRecord write happens inside
the user callback, which the class invokes inside _packet_callback's try block,
under the always-ACCEPT-in-finally guarantee. A telemetry failure is caught,
logged, and the packet is still accepted. No marking, no queue, no transmission,
no BPv7/CBOR, no uD3TN — records are born PENDING and stay PENDING.

Layer note: DSCP (RFC 2474) is read at the IP layer only. No BPv7 bundle work
here. BPv7 (RFC 9171) has no in-band priority field. Priority is enforced by
the gateway queue, never by the bundle header.

Prerequisites (each session, not persistent):
  1. docker exec upf iptables -I FORWARD -i ogstun -j NFQUEUE --queue-num 0
  2. Run this process in the UPF network namespace (--net=container:upf)
  3. Container started with --privileged
  4. Day 9: mount a host volume for run output, e.g.
       -v ~/LunaBridge/lunabridge/runs:/app/runs
"""
import logging
import os          # CHANGED (Day 9): for run_dir path join
import socket
import time        # CHANGED (Day 9): ingress_ts + run_id
from typing import Callable, Optional, Tuple

log = logging.getLogger("lunabridge.n6_interceptor")


class N6Interceptor:
    def __init__(
        self,
        queue_num: int = 0,
        interface: str = "ogstun",
        callback: Optional[Callable[[bytes, int], None]] = None,
    ):
        self.queue_num = queue_num
        self.interface = interface
        self._user_callback = callback
        self._nfqueue = None
        self._packet_count = 0

    def start(self) -> None:
        try:
            import netfilterqueue
        except ImportError as exc:
            raise RuntimeError(
                "netfilterqueue not installed. Run: pip install netfilterqueue"
            ) from exc
        log.info("N6Interceptor starting — interface=%s queue=%d",
                 self.interface, self.queue_num)
        self._nfqueue = netfilterqueue.NetfilterQueue()
        self._nfqueue.bind(self.queue_num, self._packet_callback)
        log.info("Bound to NFQUEUE %d — waiting for packets ...", self.queue_num)
        try:
            self._nfqueue.run()
        except KeyboardInterrupt:
            log.info("N6Interceptor interrupted — shutting down")
        finally:
            self._cleanup()

    def stop(self) -> None:
        self._cleanup()

    def _packet_callback(self, packet) -> None:
        try:
            payload = packet.get_payload()
            src, dst = self._extract_ips(payload)
            dscp = self._extract_dscp(payload)
            self._packet_count += 1
            log.info("pkt #%d  src=%-17s dst=%-17s dscp=%-2d  len=%d",
                     self._packet_count, src, dst, dscp, len(payload))
            if self._user_callback is not None:
                self._user_callback(payload, dscp)
        except Exception:
            log.exception("Error processing packet — still accepting")
        finally:
            # ALWAYS accept. A missing verdict wedges the kernel queue.
            packet.accept()

    @staticmethod
    def _extract_ips(payload: bytes) -> Tuple[str, str]:
        if len(payload) < 20:
            return ("?.?.?.?", "?.?.?.?")
        src = socket.inet_ntoa(payload[12:16])
        dst = socket.inet_ntoa(payload[16:20])
        return src, dst

    @staticmethod
    def _extract_dscp(payload: bytes) -> int:
        # IPv4 byte 1 = DS field: top 6 bits DSCP, bottom 2 ECN (RFC 2474)
        if len(payload) < 2:
            return 0
        return (payload[1] >> 2) & 0x3F

    def _cleanup(self) -> None:
        if self._nfqueue is not None:
            try:
                self._nfqueue.unbind()
            except Exception:
                pass
            self._nfqueue = None
        log.info("N6Interceptor stopped after %d packets", self._packet_count)


# --------------------------------------------------------------------------
# CHANGED (Day 9): module-level flow_id derivation.
# Module-level so it is importable and testable OFFLINE without the stack.
# "prototype 5-tuple-ish, NOT a BPv7 EID" (SESSION_STATE sources table).
# Contract: NEVER raises. Portless protocols (ICMP) and runt/truncated
# packets fall back gracefully — required so ICMP test traffic can't crash
# the observational path.
# --------------------------------------------------------------------------
def derive_flow_id(payload: bytes) -> str:
    if len(payload) < 20:
        return "malformed"
    ihl = (payload[0] & 0x0F) * 4
    proto = payload[9]
    src = socket.inet_ntoa(payload[12:16])
    dst = socket.inet_ntoa(payload[16:20])
    proto_name = {1: "icmp", 6: "tcp", 17: "udp"}.get(proto, str(proto))
    sport = dport = None
    if proto in (6, 17) and len(payload) >= ihl + 4:
        sport = int.from_bytes(payload[ihl:ihl + 2], "big")
        dport = int.from_bytes(payload[ihl + 2:ihl + 4], "big")
    if sport is not None:
        return f"{src}:{sport}-{dst}:{dport}-{proto_name}"
    return f"{src}-{dst}-{proto_name}"


# --------------------------------------------------------------------------
# CHANGED (Day 9): __main__ wires telemetry into the live capture path.
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import itertools

    from gateway.priority_classifier import classify_packet
    from gateway.traffic import spec
    from gateway.telemetry import BundleRecord, TelemetryWriter

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        stream=sys.stdout,
    )

    QUEUE_NUM = 0
    INTERFACE = "ogstun"
    RUNS_ROOT = os.environ.get("LUNABRIDGE_RUNS_ROOT", "/app/runs")

    start_time = time.time()
    run_id = "run-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(start_time))
    # CHANGED (Day 9): allow explicit run_dir override (reproducible runs +
    # fault-injection testing). Falls back to RUNS_ROOT/run_id.
    run_dir = os.environ.get("LUNABRIDGE_RUN_DIR", os.path.join(RUNS_ROOT, run_id))

    config = {
        "interface": INTERFACE,
        "queue_num": QUEUE_NUM,
        "run_id": run_id,
        "start_time": start_time,
        "start_time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime(start_time)),
        "clock": "container",   # ingress_ts uses container clock (prototype)
    }

    writer = TelemetryWriter(run_dir, config=config)
    bundle_seq = itertools.count(1)
    log.info("Telemetry active — run_id=%s  run_dir=%s", run_id, run_dir)

    def _on_packet(payload: bytes, dscp: int) -> None:
        """Resolve DSCP -> TrafficClass, log it, and persist one PENDING
        BundleRecord. Observational only: any failure here is logged and
        swallowed so the packet is still accepted by the caller's finally."""
        tc = classify_packet(dscp)
        s = spec(tc)
        log.info("  -> class=%-12s rank=%d  ttl=%ss  custody=%s",
                 tc.value, s.rank, int(s.default_ttl_s), s.custody_required)
        try:
            rec = BundleRecord(
                bundle_id=str(next(bundle_seq)),
                flow_id=derive_flow_id(payload),
                traffic_class=tc,
                size_bytes=len(payload),
                ingress_ts=time.time(),
            )
            rec.set_ttl()                 # class default TTL; MEDIA=0 -> exp==ingress
            writer.write_bundle(rec)
        except Exception:
            log.exception("telemetry write failed — packet still accepted")

    N6Interceptor(queue_num=QUEUE_NUM, interface=INTERFACE,
                  callback=_on_packet).start()
