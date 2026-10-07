"""
analysis/fig_channel.py

Lunar surface ACCESS channel figure from real LOLA terrain (lunaremu).
Reads a scenario's expected_links.csv (real path loss / SNR per UE over time,
computed over a PGDA LOLA 5 m/px south-pole DEM) and plots per-UE path loss,
showing the static LOS link, the diffraction-served fringe link, and the rover
driving from line of sight into terrain shadow (the coverage "cliff").

This characterizes the surface-5G island access link. NOTE: the measured N6
traffic trace in this paper was captured over the (ideal) srsRAN ZMQ access;
driving this real channel into the live loop (grc_lunar + a calibrated noise
floor) is the access-realism step reported as future work -- this figure is the
channel itself, not traffic over it.

Usage: python -m analysis.fig_channel --csv <expected_links.csv> --out figs
"""
from __future__ import annotations
import argparse, csv, os
from collections import defaultdict
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

STYLE = {  # label -> (color, pretty)
    "UE1_LOS": ("#2e7d32", "UE1 (line of sight)"),
    "UE2_fringe": ("#2c7fb8", "UE2 (diffraction fringe)"),
    "UE3_rover": ("#c0392b", "UE3 (rover → shadow)"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default="figs")
    ap.add_argument("--site", default="Connecting Ridge")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.csv)))
    t = defaultdict(list); pl = defaultdict(list); og = defaultdict(list)
    for r in rows:
        u = r["ue"]; t[u].append(float(r["t_s"]))
        pl[u].append(float(r["pl_db"])); og[u].append(int(r.get("outage", 0)))

    fig, ax = plt.subplots(figsize=(4.8, 3.0))
    for u in ["UE1_LOS", "UE2_fringe", "UE3_rover"]:
        if u not in t:
            continue
        c, lbl = STYLE[u]
        ts = np.array(t[u]) / 60.0  # minutes
        pls = np.array(pl[u])
        ax.plot(ts, pls, color=c, lw=1.8, label=lbl)
        # shade outage for the rover
        o = np.array(og[u])
        if o.any():
            ax.fill_between(ts, 0, pls, where=o > 0, color=c, alpha=0.12, step="pre")
            first = ts[np.argmax(o > 0)]
            ax.axvline(first, color=c, ls=":", lw=1)
            ax.text(first, ax.get_ylim()[1] if False else pls.max()*0.5,
                    " outage", color=c, fontsize=6.5, rotation=90, va="center")
    ax.set_xlabel("time (min)"); ax.set_ylabel("path loss (dB)")
    ax.set_title(f"Lunar surface access channel ({a.site}, LOLA 5 m/px)", fontsize=9)
    ax.legend(fontsize=6.8, loc="center left")
    ax.grid(alpha=0.3)
    fig.tight_layout(); os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "lunar_channel.pdf")
    fig.savefig(p, bbox_inches="tight"); fig.savefig(p.replace(".pdf", ".png"), dpi=150, bbox_inches="tight")
    for u in ["UE1_LOS", "UE2_fringe", "UE3_rover"]:
        if u in pl:
            print(f"{u:12s} pl {min(pl[u]):.0f}-{max(pl[u]):.0f} dB, outage {sum(og[u])}/{len(og[u])}")
    print("wrote", p)


if __name__ == "__main__":
    main()
