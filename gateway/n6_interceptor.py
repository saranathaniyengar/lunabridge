"""
gateway/n6_interceptor.py
Day 7: NFQUEUE packet capture with DSCP -> TrafficClass classification.

Changes from Day 6:
  - __main__ block now passes a callback that resolves and logs the TrafficClass
  - No other changes to the class itself
  - Always-ACCEPT-in-finally guarantee unchanged

Layer note: DSCP (RFC 2474) is read at the IP layer only. No BPv7 bundle work
here. BPv7 (RFC 9171) has no in-band priority field. Priority is enforced by
the gateway queue, never by the bundle header.

Prerequisites (each session, not persistent):
  1. docker exec upf iptables -I FORWARD -i ogstun -j NFQUEUE --queue-num 0
  2. Run this process in the UPF network namespace (--net=container:upf)
  3. Container started with --privileged
"""
import logging
import socket
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


if __name__ == "__main__":
    import sys
    from gateway.priority_classifier import classify_packet
    from gateway.traffic import spec

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        stream=sys.stdout,
    )

    def _on_packet(payload: bytes, dscp: int) -> None:
        """Resolve DSCP to TrafficClass and log it. No bundle work here."""
        tc = classify_packet(dscp)
        s = spec(tc)
        log.info("  -> class=%-12s rank=%d  ttl=%ss  custody=%s",
                 tc.value, s.rank, int(s.default_ttl_s), s.custody_required)

    N6Interceptor(queue_num=0, interface="ogstun", callback=_on_packet).start()
