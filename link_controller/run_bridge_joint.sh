#!/bin/bash
# SPDX-License-Identifier: GPL-3.0
# Live N6->BPv7 bridge with ALL FOUR classes at once (run as root on the 5G host).
# One srsUE per class, each subscriber provisioned with its class's 5QI
# (001010123456780..783 -> 69/70/9/7); bringup_4ue.sh attaches them while
# capturing N2, n2_qos_map.py extracts the four TEID -> 5QI pairs, and the bridge
# classifies every uplink packet by TEID while all four flows run concurrently.
# Usage: run_bridge_joint.sh <outdir>
#   env POLICY=fifo|strict|wfq ADMISSION=droptail|priority BUF=<bytes> RATE=<b/s>
#   runs the live gateway scheduler behind the bridge (congested contacts).
set -u
OUT=$1
D=$(cd "$(dirname "$0")" && pwd)
K(){ k3s kubectl "$@"; }
UPF_N3=10.53.1.2; DST=10.45.0.1
mkdir -p "$OUT"; exec > >(tee -a "$OUT/run.log") 2>&1
echo "[$(date +%T)] joint run: 4 UEs, 4 classes"
docker exec jrtc_open5gs mongosh --quiet open5gs --eval \
  "const q={'001010123456780':69,'001010123456781':70,'001010123456782':9,'001010123456783':7}; for (const k in q) db.subscribers.updateOne({imsi:k},{\$set:{'slice.0.session.0.qos.index':q[k]}})" >/dev/null
bash "$D/bringup_4ue.sh" 4 "$OUT/n2.pcap" | grep -q ALL_ATTACHED || { echo "bring-up failed"; exit 1; }
python3 "$D/n2_qos_map.py" "$OUT/n2.pcap" "$OUT/qos_map.json" || exit 1
UPF=$(docker inspect jrtc_open5gs --format '{{.State.Pid}}')
DUR=$(( ${WIN:-8} * 150 + 120 ))
declare -A PING=( [1]="-i 2.0 -s 120" [2]="-i 0.25 -s 500" [3]="-i 0.02 -s 1400" [4]="-i 0.04 -s 1400" )
PIDS=
for u in 1 2 3 4; do
  K exec srs-ue$u-du1-0 -n ran -c ue -- sh -c "ping -I tun_srsue ${PING[$u]} -w $DUR $DST >/dev/null 2>&1" &
  PIDS="$PIDS $!"
done
nsenter -t "$UPF" -n tcpdump -i eth0 -n -s 0 -U -w - "udp dst port 2152 and dst host $UPF_N3" 2>/dev/null \
  | env UD3TN="$D/ud3tn_bin" BRIDGE_PY="$D/venv/bin/python" UD_LOG="${UD_LOG:-3}" \
      "$D/venv/bin/python" "$D/n6_bridge.py" run \
      --plan "$D/lcrns_relay_contact_plan_1sv.csv" --windows ${WIN:-8} --k 720 \
      --qos-map "$OUT/qos_map.json" --out "$OUT" \
      ${POLICY:+--policy $POLICY --admission ${ADMISSION:-droptail} --buffer-bytes ${BUF:-2e6} --rate-bps ${RATE:-5e5}}
pkill -f "tcpdump -i eth0 -n -s 0 -U -w -"; kill $PIDS 2>/dev/null
echo "[$(date +%T)] done joint"
