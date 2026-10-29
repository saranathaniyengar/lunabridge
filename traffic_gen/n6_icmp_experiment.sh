#!/bin/bash
DUR=${1:-1200}
S(){ sudo "$@" 2>/dev/null; }
K(){ sudo k3s kubectl "$@" 2>/dev/null; }
log(){ echo "[$(date +%T)] $*"; }
UPFPID=$(S docker inspect jrtc_open5gs --format '{{.State.Pid}}')
K exec srs-ue1-du1-0 -n ran -c ue -- ip -4 addr show tun_srsue 2>/dev/null | grep -q inet || { log "UE not attached, abort"; exit 1; }
log "start UPF=$UPFPID dur=${DUR}s (robust continuous 4-class)"
S rm -f /tmp/n6_exp.pcap
S nsenter -t $UPFPID -n tcpdump -i ogstun -n -s128 -w /tmp/n6_exp.pcap 'src net 10.45.0.0/16 and not (icmp[icmptype]==icmp-echoreply)' &
sleep 2
K exec srs-ue1-du1-0 -n ran -c ue -- env DUR=$DUR sh -c '
D=10.53.1.1; IF=tun_srsue
setsid ping -Q 0x68 -i 0.25 -s 500  -w $DUR -I $IF $D >/dev/null 2>&1 &
setsid ping -Q 0xb8 -i 2.0  -s 120  -w $DUR -I $IF $D >/dev/null 2>&1 &
setsid ping -Q 0x28 -i 0.02 -s 1400 -w $DUR -I $IF $D >/dev/null 2>&1 &
setsid ping -Q 0x88 -i 0.04 -s 1400 -w $DUR -I $IF $D >/dev/null 2>&1 &
echo started'
log "traffic running: tlm(4pps) emg(0.5pps) sci(50pps) media(25pps)"
sleep $((DUR+5))
log "stopping"
K exec srs-ue1-du1-0 -n ran -c ue -- pkill -f "ping -Q" 2>/dev/null
S pkill -f "tcpdump -i ogstun" 2>/dev/null; sleep 2
S nsenter -t $UPFPID -n tcpdump -r /tmp/n6_exp.pcap -tt -nn -v 2>/dev/null > /tmp/n6_exp.txt
cd /tmp/lunabridge && python3 /tmp/txt_to_trace2.py /tmp/n6_exp.txt runs/real_srsran_scaled
log "DONE"
