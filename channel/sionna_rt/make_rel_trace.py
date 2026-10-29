#!/usr/bin/env python3
"""
channel/sionna_rt/make_rel_trace.py

Convert the absolute Sionna-RT path-loss trace written by lunar_rt_lola.py
(connecting_ridge.pathloss.json) into the RELATIVE trace that drives the live
srsRAN broker (grc_lunar_standalone.py --pathloss-trace).

The ZMQ baseband link has no absolute link budget, so path loss is applied
relative to the best line-of-sight position (pl - pl_min) on top of a
calibrated noise floor; RT shadow positions keep their explicit outage flag.
Trace time is re-based to start at 0.

Usage: python make_rel_trace.py figs/connecting_ridge.pathloss.json \
           data/connecting_ridge.rel.pathloss.json
"""
import json, sys

src, dst = sys.argv[1], sys.argv[2]
t = json.load(open(src))
los = [s["pl_db"][0] for s in t if not s["outage"][0]]
pl_min = min(los)
t0 = t[0]["t"]
rel = [{"t": round(s["t"] - t0, 2),
        "pl_db": [0.0 if s["outage"][0] else round(s["pl_db"][0] - pl_min, 2)],
        "outage": [s["outage"][0]]} for s in t]
json.dump(rel, open(dst, "w"), indent=0)
n_los = sum(1 for s in rel if not s["outage"][0])
print(f"pl_min={pl_min:.2f} dB; {len(rel)} snaps ({n_los} LOS, {len(rel)-n_los} outage); "
      f"LOS rel range 0..{max(s['pl_db'][0] for s in rel if not s['outage'][0]):.2f} dB; "
      f"first outage at t={next(s['t'] for s in rel if s['outage'][0])} s -> {dst}")
