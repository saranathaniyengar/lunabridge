#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
"""
channel/sionna_rt/plot_closed_loop_throughput.py

Closed-loop figure: a Sionna-RT lunar access-channel trace (LunaTwin-aligned,
real Connecting Ridge LOLA terrain) drives the live srsRAN downlink, and we
plot what the stack actually delivered. Only measured values are drawn:

  * delivered ICMP echo round trips per second, from a timestamped flood
    (`ping -D -O -i 0.01 -s 1200`) sent from the core to the UE; a second with
    no reply is a measured zero (the ping log records every unanswered request);
  * (gNB MAC metrics are parsed and summarized on stdout only: their absolute
    bit rate is not interpretable as UE goodput, so they are not plotted);
  * the channel state (LOS / RT outage) from the broker's trace log;
  * UE attach (PDU session) and release events from the UE log.

Inputs come from data/closedloop_v2/ (captured by its capture.sh).

Usage: python plot_closed_loop_throughput.py --dir data/closedloop_v2 \
           --out figs/closed_loop_final
"""
import argparse, os, re, sys
from datetime import datetime
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from analysis.paperstyle import apply as apply_style, COL_W  # noqa: E402
apply_style()


def k8s_ts(line):
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?([+-]\d\d:\d\d|Z)", line)
    if not m:
        return None
    frac = (m.group(2) or ".0")[:7]
    tz = m.group(3).replace("Z", "+00:00")
    return datetime.fromisoformat(m.group(1) + frac + tz).timestamp()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/closedloop_v2")
    ap.add_argument("--out", default="figs/closed_loop_final")
    ap.add_argument("--bin", type=float, default=1.0, help="ping bin width (s)")
    a = ap.parse_args()
    d = a.dir.rstrip("/") + "/"

    # channel trace steps (broker)
    steps = []
    for l in open(d + "grc.log"):
        m = re.search(r"t=(\d+)s pl_db=\[([\d.]+)\] outage=\[([01])\]", l)
        if m:
            steps.append((k8s_ts(l), int(m.group(3))))
    t0 = steps[0][0]                       # traverse starts (end of LOS hold)
    spans, s = [], None
    for t, o in steps:
        if o and s is None: s = t
        if not o and s is not None: spans.append((s - t0, t - t0)); s = None
    if s is not None: spans.append((s - t0, None))

    # ping: replies per bin (measured; empty bins are zero replies)
    rep = np.array([float(m.group(1)) for l in open(d + "ping.log")
                    for m in [re.match(r"\[(\d+\.\d+)\] \d+ bytes from", l)] if m])
    sent = np.array([float(m.group(1)) for l in open(d + "ping.log")
                     for m in [re.match(r"\[(\d+\.\d+)\] (\d+ bytes from|no answer yet)", l)] if m])
    t_end = sent.max() - t0
    edges = np.arange(0, t_end + a.bin, a.bin)
    hist, _ = np.histogram(rep - t0, bins=edges)
    rate = hist / a.bin
    first_send = sent.min() - t0

    # gNB DL MAC bitrate samples for the UE
    gt, gb = [], []
    for l in open(d + "gnb.log"):
        p = l.split()
        if "|" not in l or len(p) < 8:
            continue
        try:
            i = p.index("|") if p.count("|") else None
        except ValueError:
            i = None
        m = re.search(r"\s(\d+)\s+(\d+)\s+\|\s+\S+\s+\S+\s+(\d+)\s+(\S+)", l)
        if not m:
            continue
        br = m.group(4)
        mult = 1e-6 if br[-1].isdigit() else {"k": 1e-3, "M": 1.0}.get(br[-1], None)
        if mult is None:
            continue
        val = float(br.rstrip("kM")) * mult
        t = k8s_ts(l)
        if t:
            gt.append(t - t0); gb.append(val)

    # UE events
    attach = rel = None
    for l in open(d + "ue.log"):
        if "PDU Session Establishment successful" in l and attach is None:
            attach = k8s_ts(l) - t0
        if re.search(r"Scheduling request failed|releasing|Radio link failure", l, re.I) and rel is None:
            rel = k8s_ts(l) - t0

    fig, ax = plt.subplots(figsize=(COL_W, 1.5))
    for s0, s1 in spans:
        ax.axvspan(s0, s1 if s1 is not None else t_end, color="#2c3e50", alpha=0.12, lw=0)
    s0, s1 = spans[0]
    ax.text((s0 + (s1 or t_end)) / 2, 50, "ray-traced\noutage", ha="center",
            va="center", color="#2c3e50", fontsize=7)
    mask = edges[:-1] >= first_send
    ax.step(edges[:-1][mask], rate[mask], where="post", color="#c0392b", lw=1.0)
    ax.set_ylabel("ICMP replies/s")
    ax.set_xlabel("time since traverse start (s)")
    ax.set_xlim(0, t_end); ax.set_ylim(-4, 112)
    for x, lab in ((attach, "attach"), (rel, "UE release")):
        if x is not None:
            ax.axvline(x, color="#555", ls=":", lw=0.7)
            ax.text(x + 4, 108, lab, ha="left", color="#555", va="top", fontsize=7)
    fig.tight_layout()
    fig.savefig(a.out + ".pdf", bbox_inches="tight")
    fig.savefig(a.out + ".png", dpi=170, bbox_inches="tight")
    los = rate[(edges[:-1] > (attach or 0) + 2) & (edges[:-1] < spans[0][0])]
    out = rate[(edges[:-1] > spans[0][0] + 5) & (edges[:-1] < (spans[0][1] or t_end))]
    print(f"traverse start {datetime.utcfromtimestamp(t0):%H:%M:%S}Z; outage spans {spans}; "
          f"attach {attach:.0f}s; release {rel}")
    s1 = spans[0][1] or t_end
    n_out = int(((rep - t0 > spans[0][0]) & (rep - t0 < s1)).sum())
    print(f"LOS echoes/s mean {los.mean():.1f}; replies strictly inside the first outage "
          f"({spans[0][0]:.1f}-{s1:.1f} s): {n_out}; gNB samples {len(gb)} "
          f"(LOS median {np.median([b for t,b in zip(gt,gb) if t < spans[0][0]]) if gb else float('nan'):.2f} Mb/s)")
    print("wrote", a.out + ".pdf")


if __name__ == "__main__":
    main()
