#!/usr/bin/env python3
"""
analysis/fig_ceiling_measured.py

The lifetime-to-gap delivery law, MEASURED on real µD3TN nodes
(link_controller/ceiling_pilot.py) against the closed form

    P(L) = [ sum(contact) + sum_gaps min(L, g) ] / span

evaluated (i) on the time-compressed slice that was run live and (ii) on the
full 90-day LCRNS 1-SV plan, both plotted against rho = L / G_max so the two are
directly comparable (the law depends only on dimensionless ratios).

Usage: python -m analysis.fig_ceiling_measured --pilot <dir with results.json,
       schedule.json> --plan gateway/lcrns_relay_contact_plan_1sv.csv --out figs
"""
from __future__ import annotations
import argparse, csv, json, os, sys
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.paperstyle import apply as apply_style, COL_W  # noqa: E402
apply_style()


def law(windows, L):
    span = windows[-1][1] - windows[0][0]
    cont = sum(e - s for s, e in windows)
    gaps = [windows[i + 1][0] - windows[i][1] for i in range(len(windows) - 1)]
    return (cont + sum(min(L, g) for g in gaps)) / span, cont / span, max(gaps)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", default="figs")
    a = ap.parse_args()

    res = json.load(open(os.path.join(a.pilot, "results.json")))
    sch = json.load(open(os.path.join(a.pilot, "schedule.json")))
    Wc = [tuple(w) for w in sch["windows_rel"]]
    _, duty_c, gmax_c = law(Wc, 0)

    rows = list(csv.DictReader(open(a.plan)))
    Wf = [(float(r["start_sec"]), float(r["end_sec"])) for r in rows]
    _, duty_f, gmax_f = law(Wf, 0)

    rho = np.linspace(0, 1.6, 400)
    pc = [law(Wc, x * gmax_c)[0] for x in rho]
    pf = [law(Wf, x * gmax_f)[0] for x in rho]

    fig, ax = plt.subplots(figsize=(COL_W, 1.5))
    ax.plot(rho, pf, color="#888", lw=1.1, ls="--", label="closed form, 90-day plan")
    ax.plot(rho, pc, color="#2c3e50", label="closed form, slice")
    ax.plot([r["rho"] for r in res], [r["measured"] for r in res], "o",
            color="#c0392b", ms=4.5, mec="white", mew=0.5, zorder=5,
            label="measured (µD3TN)")
    ax.axhline(duty_c, color="#2c3e50", lw=0.6, ls=":")
    ax.axvline(1.0, color="#555", lw=0.6, ls=":")
    ax.text(1.58, duty_c - 0.003, "slice duty cycle $\\delta$", ha="right", va="top",
            color="#2c3e50", fontsize=7)
    ax.set_xlabel("lifetime-to-gap ratio $\\rho = L/G_{\\max}$")
    ax.set_ylabel("delivery ratio")
    ax.set_xlim(0, 1.6); ax.set_ylim(duty_c - 0.03, 1.01)
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 0.27))
    fig.tight_layout()
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "ceiling_measured.pdf")
    fig.savefig(p, bbox_inches="tight")
    fig.savefig(p.replace(".pdf", ".png"), dpi=170, bbox_inches="tight")
    err = [abs(r["measured"] - r["closed_form"]) for r in res]
    print(f"compressed duty={duty_c:.3f} (full {duty_f:.3f}); "
          f"max |measured - closed form| = {max(err):.4f}; wrote {p}")


if __name__ == "__main__":
    main()
