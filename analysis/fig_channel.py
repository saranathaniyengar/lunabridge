"""
analysis/fig_channel.py

Lunar surface ACCESS channel figure from real LOLA terrain (lunaremu).
Reads a scenario's expected_links.csv (real path loss / SNR per UE over time,
computed over a PGDA LOLA 5 m/px south-pole DEM) and plots per-UE path loss,
showing the static LOS link, the diffraction-served fringe link, and the rover
driving from line of sight into terrain shadow (the coverage "cliff").

This characterizes the surface-5G island access link with the lunaremu
ANALYTIC model (two-ray + Deygout / ITU-R P.526, Siegler regolith). Input data
are versioned in channel/data/connecting_ridge_4node/ (see PROVENANCE.md). The
live closed loop uses a separate Sionna-RT trace of the same site
(channel/sionna_rt/).

Usage: python -m analysis.fig_channel --csv <expected_links.csv> --out figs
"""
from __future__ import annotations
import argparse, csv, os, sys
from collections import defaultdict
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.paperstyle import apply as apply_style, COL_W  # noqa: E402
apply_style()

STYLE = {  # label -> (color, pretty)
    "UE1_LOS": ("#2e7d32", "line-of-sight user"),
    "UE2_fringe": ("#2c7fb8", "fringe user"),
    "UE3_rover": ("#c0392b", "rover"),
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

    fig, ax = plt.subplots(figsize=(COL_W, 1.78))
    for u in ["UE1_LOS", "UE2_fringe", "UE3_rover"]:
        if u not in t:
            continue
        c, lbl = STYLE[u]
        ts = np.array(t[u])  # s
        pls = np.array(pl[u])
        ax.plot(ts, pls, color=c, label=lbl)
        # shade outage for the rover
        o = np.array(og[u])
        if o.any():
            ax.fill_between(ts, 0, pls, where=o > 0, color=c, alpha=0.12, step="pre")
            first = ts[np.argmax(o > 0)]
            ax.axvline(first, color=c, ls=":", lw=0.8)
            ax.text(first + 8, 0.97 * pls.max(), "outage", color=c, va="top")
    ax.set_xlabel("time (s)"); ax.set_ylabel("path loss (dB)")
    ax.set_xlim(0, max(max(v) for v in t.values()))
    ax.legend(ncol=3, loc="lower center", bbox_to_anchor=(0.5, 1.0),
              columnspacing=1.0, handlelength=1.4)
    fig.tight_layout(); os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "lunar_channel.pdf")
    fig.savefig(p, bbox_inches="tight"); fig.savefig(p.replace(".pdf", ".png"), dpi=150, bbox_inches="tight")
    for u in ["UE1_LOS", "UE2_fringe", "UE3_rover"]:
        if u in pl:
            print(f"{u:12s} pl {min(pl[u]):.0f}-{max(pl[u]):.0f} dB, outage {sum(og[u])}/{len(og[u])}")
    print("wrote", p)


if __name__ == "__main__":
    main()
