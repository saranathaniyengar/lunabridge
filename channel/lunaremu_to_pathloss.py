#!/usr/bin/env python3
"""
channel/lunaremu_to_pathloss.py

Convert a lunaremu scenario's expected_links.csv (real south-pole terrain
channel: per-UE path loss and outage over time) into the per-UE pathloss trace
that channel/grc_lunar.py feeds to the live srsRAN ZMQ broker.

Input  : expected_links.csv  (cols: t_s, ue, pl_db, ..., outage)
Output : JSON [{"t": <s>, "pl_db": [ue1, ue2, ...], "outage": [0/1, ...]}, ...]
         with UEs in the column order given by --ue-order (default: the order
         they first appear), so it lines up with the broker's --ue-addrs.

Usage:
  python3 lunaremu_to_pathloss.py expected_links.csv -o ridge.pathloss.json \
      --ue-order UE1_LOS UE3_rover      # map trace UEs -> broker UE1, UE2
"""
import argparse, csv, json
from collections import OrderedDict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--ue-order", nargs="*", default=None,
                    help="UE names in the order the broker expects them "
                         "(subset/reorder of the CSV's UEs)")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="dB to subtract from every pl (Colosseum-style window "
                         "offset K: maps absolute lunar path loss into the "
                         "emulator's workable gain range). E.g. --offset 94 "
                         "puts the LOS link near 0 dB.")
    ap.add_argument("--max-pl", type=float, default=None,
                    help="after offset, clip pl to [0, max-pl]; a sample above "
                         "max-pl is written as an OUTAGE (terrain-shadow cliff). "
                         "Keep <= the emulator's usable attenuation (ZMQ ~25-30 dB).")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.csv_path)))
    # group by timestamp -> {ue: (pl_db, outage)}
    by_t = OrderedDict()
    seen_ues = []
    for r in rows:
        t = float(r["t_s"]); ue = r["ue"]
        if ue not in seen_ues:
            seen_ues.append(ue)
        by_t.setdefault(t, {})[ue] = (float(r["pl_db"]), int(r.get("outage", 0)))

    order = a.ue_order if a.ue_order else seen_ues
    missing = [u for u in order if u not in seen_ues]
    if missing:
        raise SystemExit(f"UE(s) {missing} not in CSV (have {seen_ues})")

    trace = []
    for t, d in by_t.items():
        pl, og = [], []
        for u in order:
            p = d[u][0] - a.offset
            o = d[u][1]
            if a.max_pl is not None and p > a.max_pl:
                o = 1              # beyond the usable window -> terrain-shadow outage
                p = a.max_pl
            if p < 0:
                p = 0.0            # offset can push LOS slightly negative -> clamp
            pl.append(round(p, 2)); og.append(o)
        trace.append({"t": t, "pl_db": pl, "outage": og})

    json.dump(trace, open(a.out, "w"))
    pls = [s["pl_db"] for s in trace]
    print(f"{len(trace)} snapshots, UEs={order}")
    print(f"  pl_db range per UE: "
          + ", ".join(f"{order[i]} {min(p[i] for p in pls):.0f}-{max(p[i] for p in pls):.0f}dB"
                      for i in range(len(order))))
    print(f"  outage snapshots: "
          + ", ".join(f"{order[i]}={sum(s['outage'][i] for s in trace)}"
                      for i in range(len(order))))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
