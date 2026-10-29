#!/bin/bash
set -euo pipefail
A="--gnb-addr $GNB_ADDR --gnb-tx-port $GNB_TX_PORT --gnb-rx-port $GNB_RX_PORT"
A="$A --slowdown 2 --samp-rate 11520000 --noise-voltage ${NOISE_VOLTAGE:-0.0}"
[ "${PROBE:-0}" = "1" ] && A="$A --probe"
[ -n "${TRACE_FILE:-}" ] && A="$A --pathloss-trace /app/$TRACE_FILE --trace-speedup ${TRACE_SPEEDUP:-1.0} --trace-start-delay ${TRACE_START_DELAY:-0} --loop-trace"
echo "[run] grc_lunar_standalone.py $A --ue-addrs '$UE_ADDRS'"
exec python3 /app/grc_lunar_standalone.py $A \
  --ue-addrs "$UE_ADDRS" --ue-tx-ports "$UE_TX_PORTS" --ue-rx-ports "$UE_RX_PORTS"
