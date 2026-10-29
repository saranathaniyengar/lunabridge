#!/bin/bash
# SPDX-License-Identifier: GPL-3.0
# One live N6->BPv7 bridge run for ONE traffic class (run as root on the 5G host).
#   1. provision the UE's PDU session with the class's 5QI (Open5GS subscriber),
#   2. re-attach the UE while capturing N2, and extract TEID -> 5QI from NGAP,
#   3. tap N3 at the UPF and run n6_bridge.py on the compressed LCRNS slice,
#      while the UE sends the class's flow (ICMP, NO DSCP marking: the class
#      comes from the 5QI only).
# Usage: run_bridge_class.sh <class> <5qi> "<ping args>" <outdir>
set -u
CLS=$1; QI=$2; PING=$3; OUT=$4
D=$(cd "$(dirname "$0")" && pwd)
K(){ k3s kubectl "$@"; }
IMSI=001010123456780; UE=srs-ue1-du1-0; UPF_N3=10.53.1.2; DST=10.45.0.1
mkdir -p "$OUT"; exec > >(tee -a "$OUT/run.log") 2>&1
echo "[$(date +%T)] class=$CLS 5QI=$QI ping='$PING'"

docker exec jrtc_open5gs mongosh --quiet open5gs --eval \
  "db.subscribers.updateOne({imsi:'$IMSI'},{\$set:{'slice.0.session.0.qos.index':$QI}})" >/dev/null
UPF=$(docker inspect jrtc_open5gs --format '{{.State.Pid}}')

# --- attach with N2 capture (retry: the ZMQ start is occasionally flaky)
for try in 1 2 3; do
  rm -f "$OUT/n2.pcap"
  nsenter -t "$UPF" -n tcpdump -i any -n -s 0 -U -w "$OUT/n2.pcap" sctp >/dev/null 2>&1 & TD=$!
  sleep 2
  K delete pod srs-gnb-du1-0 srs-grc-du1-0 $UE -n ran --wait=false >/dev/null 2>&1
  for i in $(seq 1 48); do
    sleep 5
    K logs $UE -n ran -c ue 2>/dev/null | grep -q "PDU Session Establishment successful" && break
  done
  sleep 5; kill $TD; sleep 1
  K logs $UE -n ran -c ue 2>/dev/null | grep "PDU Session Establishment" && break
  echo "attach attempt $try failed"
  [ $try = 3 ] && { echo "attach failed"; exit 1; }
done
python3 "$D/n2_qos_map.py" "$OUT/n2.pcap" "$OUT/qos_map.json" || exit 1

# --- bridge + traffic
DUR=$(( ${WIN:-8} * 150 + 120 ))
K exec $UE -n ran -c ue -- sh -c "ping -I tun_srsue $PING -w $DUR $DST >/dev/null 2>&1" &
PG=$!
nsenter -t "$UPF" -n tcpdump -i eth0 -n -s 0 -U -w - "udp dst port 2152 and dst host $UPF_N3" 2>/dev/null \
  | env UD3TN="$D/ud3tn_bin" BRIDGE_PY="$D/venv/bin/python" "$D/venv/bin/python" "$D/n6_bridge.py" run \
      --plan "$D/lcrns_relay_contact_plan_1sv.csv" --windows ${WIN:-8} --k 720 \
      --qos-map "$OUT/qos_map.json" --out "$OUT"
pkill -f "tcpdump -i eth0 -n -s 0 -U -w -" ; kill $PG 2>/dev/null
echo "[$(date +%T)] done $CLS"
