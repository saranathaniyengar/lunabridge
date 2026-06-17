"""
gateway/n6_interceptor.py
Day 6: NFQUEUE packet capture skeleton.
  - Binds to NFQUEUE 0
  - Prints src/dst IP for every intercepted packet
  - Issues ACCEPT verdict on every packet (read-only observer, no dropping yet)

Layer note: this module reads the IP-layer DSCP field (RFC 2474) only.
It does NOT build BPv7 bundles or assign bundle priority. The DSCP value
extracted here is later mapped (Day 7, priority_classifier.py) to a gateway
priority used for QUEUE ORDERING in priority_queue.py. BPv7 (RFC 9171) has
no in-band priority field, so priority is enforced by the gateway, never by
the bundle header. Nothing in this file depends on the BP version.

Prerequisites:
  1. iptables rule in UPF namespace:
       iptables -I FORWARD -i ogstun -j NFQUEUE --queue-num 0
  2. This process shares the UPF network namespace (--net=container:upf)
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
            log.info("pkt #%d  src=%-17s dst=%-17s dscp=%d  len=%d",
                     self._packet_count, src, dst, dscp, len(payload))
            # Day 7+ seam: callback receives raw IP bytes + DSCP. The gateway
            # maps DSCP -> priority for queue ordering. No bundle work here.
            if self._user_callback is not None:
                self._user_callback(payload, dscp)
        except Exception:
            log.exception("Error processing packet — still accepting")
        finally:
            # Day 6: ALWAYS accept. A missing verdict wedges the kernel queue.
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        stream=sys.stdout,
    )
    N6Interceptor(queue_num=0, interface="ogstun").start()
