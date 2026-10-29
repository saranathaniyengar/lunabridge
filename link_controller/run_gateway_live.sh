#!/bin/bash
# SPDX-License-Identifier: GPL-3.0
# Live gateway scheduler behind the bridge: three admission x drain configurations,
# four 5QI classes concurrently, congested contacts (RATE < offered load).
D=$(cd "$(dirname "$0")" && pwd); R=${1:-$D/runs/gateway_live}
mkdir -p "$R"
for cfg in "fifo droptail" "strict priority" "wfq priority"; do
  set -- $cfg
  WIN=${WIN:-5} POLICY=$1 ADMISSION=$2 BUF=${BUF:-2e6} RATE=${RATE:-5e5} \
    bash "$D/run_bridge_joint.sh" "$R/$1_$2"
done
echo ALL_DONE > "$R/ALL_DONE"
