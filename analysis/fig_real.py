"""
analysis/fig_real.py

Per-class delivery under FIFO / strict-priority / WFQ on the REAL srsRAN N6
trace (runs/real_srsran_long/bundles.jsonl -- 12,734 bundles captured over 15
min from real class-marked UE traffic through the live srsRAN+Open5GS stack,
classified at the N6/ogstun interface). A congested contact plan over the
capture span forces contention so the policies separate. This is the real-data
counterpart to fig_policy (no DES-synthetic trace).
"""
from __future__ import annotations
import argparse, os, sys, copy
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gateway.contact_plan import ContactPlan, ContactWindow
from gateway.scheduler import Scheduler, SchedulingPolicy
from gateway.telemetry import read_bundles, TerminalState
from gateway.traffic import TrafficClass

CLASSES = [TrafficClass.EMERGENCY, TrafficClass.TELEMETRY,
           TrafficClass.SCIENCE_BULK, TrafficClass.MEDIA]


def ddr(records):
    out = {}
    for cls in CLASSES:
        rs = [r for r in records if r.traffic_class is cls]
        out[cls] = (sum(1 for r in rs if r.terminal_state is TerminalState.DELIVERED)
                    / len(rs)) if rs else 0.0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="runs/real_srsran_long")
    ap.add_argument("--outdir", default="figs")
    ap.add_argument("--queue-bytes", type=float, default=500_000)
    ap.add_argument("--win-rate", type=float, default=500_000)
    ap.add_argument("--win-dur", type=float, default=25.0)
    ap.add_argument("--win-period", type=float, default=150.0)
    ap.add_argument("--win-count", type=int, default=6)
    a = ap.parse_args()

    bundles = read_bundles(a.run)
    span = max(r.ingress_ts for r in bundles)
    total_bytes = sum(r.size_bytes for r in bundles)
    plan = ContactPlan([ContactWindow(f"c{i}", i*a.win_period, i*a.win_period+a.win_dur,
                                      a.win_rate) for i in range(a.win_count)])
    budget_mbit = a.win_rate*a.win_dur*a.win_count/1e6
    print(f"real trace: {len(bundles)} bundles, {total_bytes/1e6:.2f} MB over {span:.0f}s")
    print(f"plan budget {budget_mbit:.1f} Mbit vs offered {total_bytes*8/1e6:.1f} Mbit; "
          f"queue {a.queue_bytes/1e6:.2f} MB")

    res = {}
    for name, pol in [("FIFO", SchedulingPolicy.FIFO),
                      ("strict", SchedulingPolicy.STRICT_PRIORITY),
                      ("WFQ", SchedulingPolicy.WFQ)]:
        recs = copy.deepcopy(bundles)
        Scheduler(plan, max_queue_bytes=a.queue_bytes, policy=pol).run(recs)
        res[name] = ddr(recs)
        print(f"  {name:7s}", {c.value: round(res[name][c], 3) for c in CLASSES})

    fig, ax = plt.subplots(figsize=(4.6, 2.9))
    x = np.arange(len(CLASSES)); w = 0.26
    colors = {"FIFO": "#8d8d8d", "strict": "#2c7fb8", "WFQ": "#2e7d32"}
    for i, name in enumerate(["FIFO", "strict", "WFQ"]):
        ax.bar(x+(i-1)*w, [res[name][c] for c in CLASSES], w, label=name, color=colors[name])
    ax.set_xticks(x); ax.set_xticklabels([c.value for c in CLASSES], rotation=15, fontsize=7)
    ax.set_ylabel("delivery ratio"); ax.set_ylim(0, 1.05)
    ax.set_title("Per-class delivery on the REAL srsRAN N6 trace", fontsize=9)
    ax.legend(fontsize=7, ncol=3, loc="upper center")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout(); os.makedirs(a.outdir, exist_ok=True)
    p = os.path.join(a.outdir, "real_policy.pdf")
    fig.savefig(p, bbox_inches="tight"); fig.savefig(p.replace(".pdf", ".png"), dpi=150, bbox_inches="tight")
    print("wrote", p)


if __name__ == "__main__":
    main()
