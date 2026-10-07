"""
analysis/fig_policy.py

Per-class delivery under FIFO / strict-priority / WFQ on one congested trace+plan
(DES). Shows the second-order role of scheduling: the policies diverge only when
the valuable classes contend within scarce contacts (here, a tight congested
case), and strict/WFQ protect EMERGENCY while FIFO does not.
Saves PDF+PNG to --outdir.
"""
from __future__ import annotations
import argparse, os, sys, copy
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gateway.contact_plan import ContactPlan, ContactWindow
from gateway.scheduler import Scheduler, SchedulingPolicy
from gateway.telemetry import BundleRecord, TerminalState
from gateway.traffic import TrafficClass

CLASSES = [TrafficClass.EMERGENCY, TrafficClass.TELEMETRY,
           TrafficClass.SCIENCE_BULK, TrafficClass.MEDIA]


def synth_trace(duration=30.0):
    """Deterministic class-mixed arrivals (no MGEN/pcap/server needed)."""
    b, seq = [], 0
    def add(cls, rate, size, t0, t1):
        nonlocal seq
        t = t0; iv = size / rate
        while t < t1:
            seq += 1
            r = BundleRecord(str(seq), cls.value, cls, size, ingress_ts=t)
            r.set_ttl(); b.append(r); t += iv
    add(TrafficClass.TELEMETRY, 2000, 512, 0, duration)     # 16 kbps CBR
    add(TrafficClass.EMERGENCY, 256, 128, 5, 20)            # ~2/s DURING the burst
    add(TrafficClass.SCIENCE_BULK, 250000, 1400, 5, 13)     # 2 Mbps burst
    add(TrafficClass.MEDIA, 125000, 1400, 8, 20)            # 1 Mbps EVA video
    return b


def ddr(records):
    out = {}
    for cls in CLASSES:
        rs = [r for r in records if r.traffic_class is cls]
        out[cls] = (sum(1 for r in rs if r.terminal_state is TerminalState.DELIVERED)
                    / len(rs)) if rs else 0.0
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--outdir", default="figs")
    args = ap.parse_args()
    bundles = synth_trace()
    # tight congested plan: short windows, small budget
    plan = ContactPlan([ContactWindow(f"c{i}", 2+i*6, 2+i*6+1.5, 1_000_000)
                        for i in range(6)])
    cap = 800_000
    pols = [("FIFO", SchedulingPolicy.FIFO),
            ("strict", SchedulingPolicy.STRICT_PRIORITY),
            ("WFQ", SchedulingPolicy.WFQ)]
    res = {}
    for name, pol in pols:
        s = Scheduler(plan, max_queue_bytes=cap, policy=pol)
        recs = copy.deepcopy(bundles); s.run(recs); res[name] = ddr(recs)

    fig, ax = plt.subplots(figsize=(4.4, 2.8))
    x = np.arange(len(CLASSES)); w = 0.26
    colors = {"FIFO": "#8d8d8d", "strict": "#2c7fb8", "WFQ": "#2e7d32"}
    for i, (name, _) in enumerate(pols):
        vals = [res[name][c] for c in CLASSES]
        ax.bar(x + (i-1)*w, vals, w, label=name, color=colors[name])
    ax.set_xticks(x); ax.set_xticklabels([c.value for c in CLASSES],
                                         rotation=15, fontsize=7)
    ax.set_ylabel("delivery ratio"); ax.set_ylim(0, 1.05)
    ax.set_title("Per-class delivery by policy (congested)", fontsize=9)
    ax.legend(fontsize=7, ncol=3, loc="upper center")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    os.makedirs(args.outdir, exist_ok=True)
    p = os.path.join(args.outdir, "policy_compare.pdf")
    fig.savefig(p, bbox_inches="tight")
    fig.savefig(p.replace(".pdf", ".png"), dpi=150, bbox_inches="tight")
    for name, _ in pols:
        print(name, {c.value: round(res[name][c], 3) for c in CLASSES})
    print("wrote", p)


if __name__ == "__main__":
    main()
