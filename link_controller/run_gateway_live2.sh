#!/bin/bash
# SPDX-License-Identifier: GPL-3.0
# Remaining three admission x drain configurations of the live gateway
# (completes the 2 x 3 cross with run_gateway_live.sh).
D=$(cd "$(dirname "$0")" && pwd); R=${1:-$D/runs/gateway_live}
mkdir -p "$R"
for cfg in "fifo priority" "strict droptail" "wfq droptail"; do
  set -- $cfg
  WIN=${WIN:-5} POLICY=$1 ADMISSION=$2 BUF=${BUF:-2e6} RATE=${RATE:-5e5} \
    bash "$D/run_bridge_joint.sh" "$R/$1_$2"
done
echo ALL_DONE > "$R/ALL_DONE2"
