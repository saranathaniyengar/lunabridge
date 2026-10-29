"""
analysis/fig_real.py

Per-class delivery under FIFO / strict-priority / WFQ (work-conserving DRR)
when the gateway scheduler is replayed over the REAL N6 trace:
runs/real_srsran_clean/bundles.jsonl -- 81,662 bundle records, 1,200 s,
captured at ogstun (UPF, N6) on the live srsRAN gNB + srsUE (ZMQ, ideal
channel) + Open5GS stack while one UE sent four DSCP-marked ICMP flows
(traffic_gen/n6_icmp_experiment.sh; raw pcap in runs/real_srsran_capture/),
converted with traffic_gen/n6_text_to_trace.py (each packet classified from
its DSCP; per-bundle lifetimes from CLASS_SPECS at capture time: 23,627 /
47,254 / 165,389 / 11,813 s).

Defaults reproduce the paper figure: a congested test plan of nine 8 s
contacts at 10 Mb/s every 150 s (a congestion test, not the LCRNS plan; contacts start at t=0, so the first
finds an almost empty buffer), 720 Mbit of nominal capacity against 893 Mbit
offered, and a 30 MB buffer. Under every policy 646 Mbit is delivered, 87 Mbit
is dropped at admission and 160 Mbit is still queued when the last contact
closes; policies differ only in which class absorbs those losses.
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
from analysis.paperstyle import apply as apply_style, COL_W  # noqa: E402
apply_style()

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
    ap.add_argument("--run", default="runs/real_srsran_clean")
    ap.add_argument("--outdir", default="figs")
    ap.add_argument("--queue-bytes", type=float, default=30_000_000)
    ap.add_argument("--win-rate", type=float, default=10_000_000)
    ap.add_argument("--win-dur", type=float, default=8.0)
    ap.add_argument("--win-period", type=float, default=150.0)
    ap.add_argument("--win-count", type=int, default=9)
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

    # admission x drain: every combination on the same trace and plan (LB-H4)
    combos = [("FIFO", "drop-tail", SchedulingPolicy.FIFO, False),
              ("FIFO", "priority-aware", SchedulingPolicy.FIFO, True),
              ("strict", "drop-tail", SchedulingPolicy.STRICT_PRIORITY, False),
              ("strict", "priority-aware", SchedulingPolicy.STRICT_PRIORITY, True),
              ("WFQ", "drop-tail", SchedulingPolicy.WFQ, False),
              ("WFQ", "priority-aware", SchedulingPolicy.WFQ, True)]
    lines = []
    for drain, adm, pol, pa in combos:
        recs = copy.deepcopy(bundles)
        Scheduler(plan, max_queue_bytes=a.queue_bytes, policy=pol,
                  priority_aware_admission=pa).run(recs)
        r = ddr(recs)
        # share of each class dropped at admission (the rest of the undelivered
        # bundles are still queued when the last contact closes)
        drop = {}
        for c in CLASSES:
            rs = [x for x in recs if x.traffic_class is c]
            drop[c] = sum(x.terminal_state is TerminalState.QUEUE_OVERFLOW for x in rs) / len(rs)
        mbit = sum(x.size_bytes for x in recs
                   if x.terminal_state is TerminalState.DELIVERED) * 8 / 1e6
        print(f"  {drain:6s} {adm:14s}", {c.value: (round(r[c], 3), round(drop[c], 3))
                                          for c in CLASSES}, f"{mbit:.0f} Mbit")
        lines.append(f"{drain} & {adm} & " + " & ".join(
            f"{100 * r[c]:.0f} ({100 * drop[c]:.0f})" for c in CLASSES) + f" & {mbit:.0f} \\\\")
    os.makedirs(a.outdir, exist_ok=True)
    open(os.path.join(a.outdir, "admission_drain_rows.tex"), "w").write("\n".join(lines) + "\n")

    fig, ax = plt.subplots(figsize=(COL_W, 1.65))
    x = np.arange(len(CLASSES)); w = 0.26
    colors = {"FIFO": "#8d8d8d", "strict": "#2c7fb8", "WFQ": "#2e7d32"}
    labels = {"FIFO": "FIFO", "strict": "strict priority", "WFQ": "WFQ (DRR)"}
    for i, name in enumerate(["FIFO", "strict", "WFQ"]):
        ax.bar(x+(i-1)*w, [res[name][c] for c in CLASSES], w, label=labels[name],
               color=colors[name])
    ax.set_xticks(x)
    ax.set_xticklabels(["Emergency", "Telemetry", "Science", "Media"])
    ax.set_ylabel("delivery ratio"); ax.set_ylim(0, 1.05)
    ax.legend(ncol=3, loc="lower center", bbox_to_anchor=(0.5, 1.0),
              handlelength=1.2, columnspacing=1.0)
    ax.grid(axis="x", visible=False)
    fig.tight_layout(); os.makedirs(a.outdir, exist_ok=True)
    p = os.path.join(a.outdir, "real_policy.pdf")
    fig.savefig(p, bbox_inches="tight"); fig.savefig(p.replace(".pdf", ".png"), dpi=150, bbox_inches="tight")
    print("wrote", p)


if __name__ == "__main__":
    main()
