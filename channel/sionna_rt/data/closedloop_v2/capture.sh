#!/bin/bash
# Closed-loop capture: Sionna-RT (LunaTwin-aligned) trace -> live srsRAN DL.
# Records gNB metrics, broker trace steps, UE RRC log, and a timestamped ICMP
# flood from the core to the UE (each reply/loss logged; no post-processing).
D=/home/nuwins-server1/closedloop_v2; DUR=${1:-560}
K(){ sudo k3s kubectl "$@" 2>/dev/null; }
K logs -f --timestamps srs-gnb-du1-0 -n ran -c gnb  > $D/gnb.log 2>&1 & P1=$!
K logs -f --timestamps srs-grc-du1-0 -n ran -c grc  > $D/grc.log 2>&1 & P2=$!
K logs -f --timestamps srs-ue1-du1-0 -n ran -c ue   > $D/ue.log  2>&1 & P3=$!
sudo docker exec jrtc_open5gs sh -c "ping -D -O -i 0.01 -s 1200 10.45.0.2" > $D/ping.log 2>&1 & P4=$!
date -u +%FT%T.%NZ > $D/start_utc.txt
sleep $DUR
sudo docker exec jrtc_open5gs pkill ping 2>/dev/null
kill $P1 $P2 $P3 $P4 2>/dev/null
date -u +%FT%T.%NZ > $D/end_utc.txt; touch $D/DONE
