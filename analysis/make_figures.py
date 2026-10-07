"""
analysis/make_figures.py

Generate the paper's data-ready figures from the DES / sizing model (no live
stack needed). Saves vector PDF (for LaTeX) + PNG (preview) into --outdir.

Figure 1 -- buffer during blackout + sizing:
  (A) on-board buffer occupancy vs. time across the worst ELFO gap, for three
      offered loads (video-dominated, science, critical-only), against a
      CM4-class 256 MB buffer. Video saturates in minutes; the critical classes
      never fill it within the gap.
  (B) buffer required to guarantee zero safety-of-life loss vs. blackout
      duration -- native BPv7 (class-blind admission, must hold the whole
      backlog) vs. priority-aware admission (critical backlog only): ~2 orders
      of magnitude less at equal guarantee.

Usage: python -m analysis.make_figures --outdir figs
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.buffer_sizing import real_max_gap, MISSION_PROFILE, analytical  # noqa: E402

MB = 1e6
GB = 1e9
CM4_BUFFER = 256 * MB  # CM4-class on-board buffer


def fig_buffer_blackout(outdir: str) -> str:
    gmax = real_max_gap()                      # s, real LCRNS 1-SV max gap
    a = analytical(MISSION_PROFILE, gmax)      # total/critical rates, thresholds

    total_rate = a["total_rate"]               # science-dominated profile, B/s
    crit_rate = a["crit_rate"]                 # emergency + telemetry, B/s
    video_rate = 12e6 / 8                       # 12 Mbps island video feed, B/s

    fig, (axA, axB) = plt.subplots(1, 2, figsize=(7.2, 2.9))

    # -- Panel A: occupancy vs time across the worst gap --------------------
    t = np.linspace(0, gmax, 400)              # s
    loads = [
        ("video-dominated (12 Mbps)", video_rate, "#c0392b"),
        ("science (2 Mbps)", total_rate, "#2c7fb8"),
        ("critical only (tlm+emg)", crit_rate, "#2e7d32"),
    ]
    for label, rate, color in loads:
        occ = np.minimum(rate * t, CM4_BUFFER) / MB
        axA.plot(t / 3600, occ, label=label, color=color, lw=2)
        # mark saturation time if it saturates within the gap
        t_sat = CM4_BUFFER / rate
        if t_sat < gmax:
            axA.plot(t_sat / 3600, CM4_BUFFER / MB, "o", color=color, ms=4)
    axA.axhline(CM4_BUFFER / MB, ls="--", color="k", lw=1)
    axA.text(0.02, CM4_BUFFER / MB * 1.03, "CM4-class 256 MB",
             fontsize=7, va="bottom")
    axA.set_xlabel("time into blackout (h)")
    axA.set_ylabel("buffer occupancy (MB)")
    axA.set_title("(A) occupancy during the worst gap", fontsize=9)
    axA.set_xlim(0, gmax / 3600)
    axA.set_ylim(0, CM4_BUFFER / MB * 1.25)
    axA.legend(fontsize=6.5, loc="upper right")
    axA.grid(alpha=0.3)

    # -- Panel B: required buffer vs gap, native vs priority-aware ----------
    gaps = np.linspace(60, gmax, 200)          # s
    axB.plot(gaps / 3600, total_rate * gaps / GB, color="#c0392b", lw=2,
             label="native BPv7 (whole backlog)")
    axB.plot(gaps / 3600, crit_rate * gaps / GB, color="#2e7d32", lw=2,
             label="priority-aware (critical only)")
    axB.set_yscale("log")
    axB.set_xlabel("blackout duration (h)")
    axB.set_ylabel("buffer for zero safety loss (GB)")
    axB.set_title("(B) buffer to guarantee EVA-safety", fontsize=9)
    ratio = a["ratio"]
    axB.annotate(f"{ratio:.0f}$\\times$ at $G_{{\\max}}$",
                 xy=(gmax / 3600, a["B_native"] / GB),
                 xytext=(gmax / 3600 * 0.45, a["B_native"] / GB * 0.5),
                 fontsize=8, arrowprops=dict(arrowstyle="->", lw=1))
    axB.axvline(gmax / 3600, ls=":", color="gray", lw=1)
    axB.text(gmax / 3600, axB.get_ylim()[0] * 1.5,
             f"$G_{{\\max}}{{=}}{gmax/3600:.2f}$ h", fontsize=7,
             ha="right", rotation=90, va="bottom")
    axB.legend(fontsize=6.5, loc="lower right")
    axB.grid(alpha=0.3, which="both")

    fig.tight_layout()
    os.makedirs(outdir, exist_ok=True)
    pdf = os.path.join(outdir, "buffer_blackout.pdf")
    png = os.path.join(outdir, "buffer_blackout.png")
    fig.savefig(pdf, bbox_inches="tight")
    fig.savefig(png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    # report the key numbers used
    print(f"G_max={gmax:.0f}s ({gmax/3600:.2f}h); "
          f"native={a['B_native']/GB:.2f}GB priority={a['B_critical']/MB:.0f}MB "
          f"ratio={ratio:.0f}x")
    print(f"video saturates 256MB in {CM4_BUFFER/video_rate/60:.1f} min; "
          f"science in {CM4_BUFFER/total_rate/60:.1f} min; "
          f"critical in {CM4_BUFFER/crit_rate/3600:.1f} h")
    return pdf


def fig_buffer_required(outdir: str) -> str:
    """Column-width version: buffer needed to guarantee zero safety-of-life loss
    vs blackout length, native BPv7 vs priority-aware admission, with the
    CM4-class 256 MB reference (analytic: rate x gap)."""
    gmax = real_max_gap()
    a = analytical(MISSION_PROFILE, gmax)
    total_rate, crit_rate = a["total_rate"], a["crit_rate"]
    gaps = np.linspace(60, gmax, 200)
    fig, ax = plt.subplots(figsize=(3.5, 2.3))
    ax.plot(gaps / 3600, total_rate * gaps / GB, color="#c0392b", lw=2,
            label="native BPv7 (whole backlog)")
    ax.plot(gaps / 3600, crit_rate * gaps / GB, color="#2e7d32", lw=2,
            label="priority-aware (critical only)")
    ax.axhline(CM4_BUFFER / GB, ls="--", color="k", lw=0.9)
    ax.text(0.05, CM4_BUFFER / GB * 1.15, "CM4-class 256 MB", fontsize=6.5)
    ax.set_yscale("log")
    ax.set_xlabel("blackout duration (h)")
    ax.set_ylabel("buffer for zero\nsafety loss (GB)")
    xg = gmax / 3600 * 0.985
    ax.annotate("", xy=(xg, a["B_native"] / GB), xytext=(xg, a["B_critical"] / GB),
                arrowprops=dict(arrowstyle="<->", lw=0.9, color="#333"))
    ax.text(xg - 0.12, 0.9, f"{a['ratio']:.0f}$\\times$", fontsize=8,
            ha="right", va="center", color="#333")
    ax.axvline(gmax / 3600, ls=":", color="gray", lw=0.9)
    ax.set_xlim(0, gmax / 3600 * 1.02)
    ax.legend(fontsize=6.2, loc="lower right")
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    os.makedirs(outdir, exist_ok=True)
    pdf = os.path.join(outdir, "buffer_required.pdf")
    fig.savefig(pdf, bbox_inches="tight")
    fig.savefig(pdf.replace(".pdf", ".png"), dpi=170, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {pdf}: native={a['B_native']/GB:.2f} GB, "
          f"priority-aware={a['B_critical']/MB:.0f} MB, ratio={a['ratio']:.0f}x")
    return pdf


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="figs")
    args = ap.parse_args()
    pdf = fig_buffer_blackout(args.outdir)
    print(f"wrote {pdf}")


if __name__ == "__main__":
    main()
