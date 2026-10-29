#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
"""
Plot the CLOSED LOOP: Sionna-RT lunar channel (real Connecting Ridge terrain)
driving the live srsRAN link. Correlates the broker's played path-loss trace with
the gNB's per-UE link activity -- link UP in LOS, BLACKOUT at the crater-rim
cliff (RT outage), RECOVERS when the rover loops back to LOS.

Input: /tmp/trace_series.txt (broker log, 't=..s pl_db=.. outage=..') and
/tmp/gnb_series.txt (gNB metrics, '100 <rnti> .. mcs .. brate'), both --timestamps.
"""
import re, os
from datetime import datetime
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

def ts(line):
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)", line)
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    return base.timestamp() + float("0." + m.group(2))

# broker trace: wall time, rel path loss, outage
tr_t, tr_pl, tr_og = [], [], []
for ln in open("/tmp/trace_series.txt"):
    if "pl_db" not in ln: continue
    pl = float(re.search(r"pl_db=\[?([0-9.]+)", ln).group(1))
    og = int(re.search(r"outage=\[?([01])", ln).group(1))
    tr_t.append(ts(ln)); tr_pl.append(pl); tr_og.append(og)
# gNB link activity: wall time, brate(k)
g_t, g_br = [], []
for ln in open("/tmp/gnb_series.txt"):
    mb = re.search(r"brate=([0-9.]+)([kM]?)", ln.replace("|", " "))
    # robust: 6th field after '100'
    parts = ln.split()
    try:
        i = parts.index("100"); br = parts[i+6]
    except Exception:
        continue
    mult = 1e-3 if br.endswith("k") else (1.0 if br.endswith("M") else 1e-6)
    val = float(re.sub(r"[kM]$", "", br)) * mult  # Mbps
    g_t.append(ts(ln)); g_br.append(val)

t0 = min(tr_t + g_t)
tr_t = np.array(tr_t) - t0; g_t = np.array(g_t) - t0
tr_pl = np.array(tr_pl); tr_og = np.array(tr_og); g_br = np.array(g_br)
# show one full cycle + recovery
tmax = 240
m = tr_t <= tmax; tr_t, tr_pl, tr_og = tr_t[m], tr_pl[m], tr_og[m]
gm = g_t <= tmax; g_t, g_br = g_t[gm], g_br[gm]

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(6.2, 4.2), sharex=True,
                               gridspec_kw={"height_ratios": [1, 1]})
# panel A: RT channel
pl_plot = np.where(tr_og == 1, np.nan, tr_pl)
ax1.plot(tr_t, pl_plot, color="#c0392b", lw=1.8, marker="o", ms=2.5)
# shade outage spans
def spans(t, og):
    out = []; s = None
    for i in range(len(t)):
        if og[i] and s is None: s = t[i]
        if not og[i] and s is not None: out.append((s, t[i])); s = None
    if s is not None: out.append((s, t[-1]))
    return out
for (s, e) in spans(tr_t, tr_og):
    ax1.axvspan(s, e, color="#2c3e50", alpha=0.12)
    ax2.axvspan(s, e, color="#2c3e50", alpha=0.12)
    ax1.text((s+e)/2, 5.5, "crater-rim\nshadow\n(RT outage)", ha="center",
             va="center", fontsize=7, color="#2c3e50")
ax1.set_ylabel("RT channel\npath loss (dB rel.)", fontsize=8)
ax1.set_title("Closed loop: Sionna-RT lunar channel (real LOLA terrain) → live srsRAN",
              fontsize=9)
ax1.grid(alpha=0.3); ax1.invert_yaxis()
# panel B: srsRAN link state
up = g_br  # Mbps; connected samples
ax2.plot(g_t, up, color="#2e7d32", lw=1.3, marker="s", ms=3, label="srsRAN UE link")
ax2.set_ylabel("UE DL rate\n(Mbps)", fontsize=8)
ax2.set_xlabel("time (s)")
ax2.grid(alpha=0.3)
ax2.annotate("link blackout\nduring shadow", xy=(0.5, 0.5), xycoords="axes fraction",
             ha="center", fontsize=7, color="#2c3e50")
fig.tight_layout()
p = "figs/closed_loop.pdf"; fig.savefig(p, bbox_inches="tight")
fig.savefig(p.replace(".pdf", ".png"), dpi=150, bbox_inches="tight")
print("wrote", p)
print(f"LOS pts: {int(np.sum(tr_og==0))}, outage pts: {int(np.sum(tr_og==1))}; "
      f"gNB link samples: {len(g_t)} (gap during outage = link down)")
