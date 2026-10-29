#!/bin/bash
# Regenerate every figure of the ICC 2027 paper from versioned data.
# Usage: ./figures.sh [paper_figures_dir]   (default: ../lunabridge-paper/figures)
set -euo pipefail
cd "$(dirname "$0")"
OUT=${1:-../lunabridge-paper/figures}
TMP=$(mktemp -d)
# Fig. channel: lunaremu analytic access channel, Connecting Ridge
python3 analysis/fig_channel.py --csv channel/data/connecting_ridge_4node/expected_links.csv --out "$TMP"
cp "$TMP/lunar_channel.pdf" "$OUT/"
# Fig. admissibility: closed form over the real LCRNS 1-SV plan, 50 Mb/s return
python3 analysis/fig_admissibility.py --closed --rate-bps 50e6 --outdir "$TMP"
cp "$TMP/admissibility.pdf" "$OUT/"
# Fig. real: gateway scheduler replayed over the 81,662-bundle N6 capture
#   (bundles.jsonl is versioned gzipped in runs/real_srsran_capture/)
[ -f runs/real_srsran_clean/bundles.jsonl ] || { mkdir -p runs/real_srsran_clean; gunzip -c runs/real_srsran_capture/bundles_81662.jsonl.gz > runs/real_srsran_clean/bundles.jsonl; }
python3 analysis/fig_real.py --outdir "$TMP"
cp "$TMP/real_policy.pdf" "$OUT/"
# Fig. ceiling: real µD3TN compressed-clock measurement
python3 analysis/fig_ceiling_measured.py --pilot runs/ceiling_pilot --plan gateway/lcrns_relay_contact_plan_1sv.csv --out "$TMP"
cp "$TMP/ceiling_measured.pdf" "$OUT/"
# Table I: link budget from the true site-relay slant range (prints only)
python3 analysis/link_budget.py
# Fig. buffer: analytic buffer sizing
python3 analysis/make_figures.py --outdir "$TMP"
cp "$TMP/buffer_required.pdf" "$OUT/"
# Fig. closed loop: Sionna-RT trace driving the live srsRAN downlink (needs the sionna venv only for RT, not for plotting)
python3 channel/sionna_rt/plot_closed_loop_throughput.py --dir channel/sionna_rt/data/closedloop_v2 --out "$TMP/closed_loop_final"
cp "$TMP/closed_loop_final.pdf" "$OUT/"
# Table: live N6->BPv7 bridge with 5QI classes (runs/live_5qi, link_controller/run_bridge_class.sh)
python3 analysis/live_bridge_table.py --runs runs/live_5qi --out "$OUT/live_bridge_rows.tex"
# Table: live gateway scheduler behind the bridge vs offline replay (runs/gateway_live)
python3 analysis/live_vs_replay.py --runs runs/gateway_live --out "$OUT/live_gateway_rows.tex"
echo "figures written to $OUT"
