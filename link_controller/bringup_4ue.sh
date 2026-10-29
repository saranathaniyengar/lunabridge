#!/bin/bash
# SPDX-License-Identifier: GPL-3.0
# Bring up 4 srsUEs on the ZMQ broker (run as root on the 5G host). Each UE gets
# a different UL/DL attenuation in the broker (UE_PATHLOSS) so that when their
# identical RACH preambles collide, the strongest Msg3 still decodes (capture)
# and the UEs attach one after another. Retries the whole start-up.
# Usage: bringup_4ue.sh [attempts] [n2_pcap]
set -u
N=${1:-4}; PCAP=${2:-}
K(){ k3s kubectl "$@"; }
UES="srs-ue1-du1-0 srs-ue2-du1-0 srs-ue3-du1-0 srs-ue4-du1-0"
K scale sts srs-ue2-du1 srs-ue3-du1 srs-ue4-du1 -n ran --replicas=1 >/dev/null
K set env sts/srs-grc-du1 -n ran \
  UE_ADDRS="srs-ue1-du1-zmq.ran.svc.cluster.local srs-ue2-du1-zmq.ran.svc.cluster.local srs-ue3-du1-zmq.ran.svc.cluster.local srs-ue4-du1-zmq.ran.svc.cluster.local" \
  UE_TX_PORTS="2101 2201 2301 2401" UE_RX_PORTS="2100 2200 2300 2400" \
  UE_PATHLOSS="${UE_PATHLOSS:-0 6 12 18}" SLOWDOWN="${SLOWDOWN:-4}" >/dev/null
UPF=$(docker inspect jrtc_open5gs --format '{{.State.Pid}}')
for a in $(seq 1 $N); do
  echo "[$(date +%T)] start-up attempt $a"
  TD=
  if [ -n "$PCAP" ]; then
    rm -f "$PCAP"; nsenter -t "$UPF" -n tcpdump -i any -n -s 0 -U -w "$PCAP" sctp >/dev/null 2>&1 & TD=$!
    sleep 2
  fi
  K delete pod srs-gnb-du1-0 srs-grc-du1-0 $UES -n ran --wait=false >/dev/null 2>&1
  ok=0
  for i in $(seq 1 60); do
    sleep 5
    ok=0; for u in $UES; do K logs $u -n ran -c ue 2>/dev/null | grep -q "PDU Session Establishment successful" && ok=$((ok+1)); done
    [ $ok = 4 ] && break
    # nobody even sent a preamble after 2 min: the ZMQ start stalled, restart
    if [ $i = 24 ]; then
      ra=0; for u in $UES; do K logs $u -n ran -c ue 2>/dev/null | grep -q "Random Access" && ra=1; done
      [ $ra = 0 ] && break
    fi
  done
  for u in $UES; do echo "  $u: $(K logs $u -n ran -c ue 2>/dev/null | grep -c 'Random Access Trans') RA, $(K logs $u -n ran -c ue 2>/dev/null | grep 'PDU Session Establishment' | tail -1)"; done
  [ -n "$TD" ] && { sleep 3; kill $TD; }
  [ $ok = 4 ] && { echo "ALL_ATTACHED"; exit 0; }
done
echo "BRINGUP_FAILED"; exit 1
