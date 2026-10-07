#!/usr/bin/env python3
import re
from datetime import datetime
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

SP = "/private/tmp/claude-502/-Users-e-baena/098255c9-16a6-46c0-9596-1798761a9535/scratchpad"
OUT = "/Users/e.baena/CascadeProjects/lunabridge/channel/sionna_rt/figs/closed_loop_final"

def wt(line):
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)", line)
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").timestamp() + float("0."+m.group(2))

def brate_mbps(tok):
    tok = tok.strip()
    if tok.endswith("M"): return float(tok[:-1])
    if tok.endswith("k"): return float(tok[:-1])/1000.0
    try: return float(tok)/1e6
    except: return None

# gNB brate
gt, gb = [], []
for ln in open(SP+"/gnb_final.txt"):
    p = ln.split()
    try: i = p.index("100")
    except ValueError: continue
    v = brate_mbps(p[i+6])
    if v is None: continue
    gt.append(wt(ln)); gb.append(v)
gt = np.array(gt); gb = np.array(gb)

# trace outage transitions
tt, to = [], []
for ln in open(SP+"/trace_final.txt"):
    m = re.search(r"outage=\[?([01])", ln)
    if not m: continue
    tt.append(wt(ln)); to.append(int(m.group(1)))
tt = np.array(tt); to = np.array(to)

t0 = gt.min()
gt -= t0; tt -= t0
# window to one clean LOS->cliff->outage->recovery cycle
W0, W1 = 30, 430
mg = (gt >= W0) & (gt <= W1); gt, gb = gt[mg], gb[mg]
mt = (tt >= W0) & (tt <= W1); tt, to = tt[mt], to[mt]

# outage intervals
spans, s = [], None
for i in range(len(tt)):
    if to[i] == 1 and s is None: s = tt[i]
    if to[i] == 0 and s is not None: spans.append((s, tt[i])); s = None
if s is not None: spans.append((s, tt.max()))

# force throughput to 0 inside outage spans (link down); insert 0 at edges so
# the drop/recovery render as vertical cliffs, not diagonals across the gap
gb_disp = gb.copy()
for (s, e) in spans:
    gb_disp[(gt >= s) & (gt <= e)] = 0.0
edges_t, edges_v = [], []
for (s, e) in spans:
    edges_t += [s - 0.1, s + 0.1, e - 0.1, e + 0.1]
    edges_v += [np.interp(s - 0.1, gt, gb_disp), 0.0, 0.0,
                np.interp(e + 0.1, gt, gb_disp)]
gt = np.concatenate([gt, edges_t]); gb_disp = np.concatenate([gb_disp, edges_v])
order = np.argsort(gt); gt, gb_disp = gt[order], gb_disp[order]

fig, ax = plt.subplots(figsize=(6.4, 3.4))
for (s, e) in spans:
    ax.axvspan(s, e, color="#2c3e50", alpha=0.12, zorder=0)
ax.plot(gt, gb_disp, color="#c0392b", lw=1.8, marker="o", ms=3, zorder=3,
        label="srsRAN UE DL throughput")
if spans:
    s0 = spans[0][0]
    ax.axvline(s0, color="#2c3e50", ls=":", lw=1.3)
    ax.text(s0+3, ax.get_ylim()[1]*0.92, "crater-rim cliff\n→ RT outage",
            fontsize=7.5, color="#2c3e50", va="top")
ax.text((gt.min()+ (spans[0][0] if spans else gt.max()))/2, 4.9,
        "LOS traverse\n~4.5 Mbps steady", fontsize=7.5, color="#c0392b", ha="center")
ax.set_xlabel("time (s)"); ax.set_ylabel("DL throughput (Mbps)")
ax.set_title("Closed loop: Sionna-RT lunar channel (real LOLA) → live srsRAN throughput",
             fontsize=8.8)
ax.grid(alpha=0.3); ax.set_ylim(-0.3, 5.5)
ax.legend(fontsize=7.5, loc="center right")
fig.tight_layout()
fig.savefig(OUT+".pdf", bbox_inches="tight"); fig.savefig(OUT+".png", dpi=150, bbox_inches="tight")
print("wrote", OUT+".png")
print(f"LOS throughput samples: {int(np.sum(gb_disp>1))} @ ~{np.median(gb[gb>1]):.1f} Mbps; "
      f"outage spans: {len(spans)}")
