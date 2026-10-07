"""
analysis/fig_admissibility.py

Central figure: per-class delivery fraction vs bundle TTL over the REAL LCRNS
1-SV contact plan. Each class is run ALONE at its sustained rate (the
admissibility law is per-class), with a huge buffer so the ONLY loss modes are
gap-limited (TTL expiry in a blackout) and capacity-limited (offered rate vs
backhaul). Run `--verify` first to print plan stats and per-class terminal-state
breakdowns and sanity-check the pipeline before trusting the curves.
"""
from __future__ import annotations
import argparse, csv, os, sys, copy
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gateway.contact_plan import ContactPlan, ContactWindow
from gateway.scheduler import Scheduler, SchedulingPolicy
from gateway.telemetry import BundleRecord, TerminalState
from gateway.traffic import TrafficClass

CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "gateway", "lcrns_relay_contact_plan_1sv.csv")
HUGE = 1e18  # buffer cap: isolate gap+capacity, never overflow

# per-class sustained offered rate (bytes/s). Mission profile (documented).
RATE = {
    TrafficClass.EMERGENCY:    128 / 60.0,   # ~1 small alert/min
    TrafficClass.TELEMETRY:    2000.0,       # ~16 kbps
    TrafficClass.SCIENCE_BULK: 250000.0,     # ~2 Mbps
    TrafficClass.MEDIA:        1.5e6,        # ~12 Mbps video
}


def load_plan():
    W = []
    rows = list(csv.DictReader(open(CSV)))
    for i, r in enumerate(rows):
        W.append(ContactWindow(f"c{i}", float(r["start_sec"]), float(r["end_sec"]),
                               float(r["rate_bps"])))
    return ContactPlan(W), rows


def plan_stats(plan, rows):
    starts = [w.start_ts for w in plan._windows]
    ends = [w.end_ts for w in plan._windows]
    span = ends[-1] - starts[0]
    contact = sum(e - s for s, e in zip(starts, ends))
    gaps = [starts[i+1] - ends[i] for i in range(len(starts)-1)]
    budget_bits = sum(w.raw_bit_budget() for w in plan._windows)
    return dict(n=len(plan), span=span, contact=contact, duty=contact/span,
                gmax=max(gaps), gmed=float(np.median(gaps)),
                avg_bh_bps=budget_bits/span)


def run_class(plan, cls, ttl_s, span, dt, rate_override=None):
    """One class alone, sustained arrivals over the plan span, bundle TTL=ttl_s.
    rate_override (bytes/s) replaces the class default (used for the video sweep)."""
    rate = RATE[cls] if rate_override is None else rate_override
    size = max(1, int(rate * dt))
    b, seq = [], 0
    t = plan._windows[0].start_ts
    end = plan._windows[-1].end_ts
    while t < end:
        seq += 1
        r = BundleRecord(str(seq), cls.value, cls, size, ingress_ts=t)
        r.set_ttl(ttl_s)
        b.append(r); t += dt
    s = Scheduler(plan, max_queue_bytes=HUGE, policy=SchedulingPolicy.STRICT_PRIORITY)
    s.run(b)
    tot = len(b)
    cnt = {st: 0 for st in TerminalState}
    for r in b:
        cnt[r.terminal_state] += 1
    deliv = cnt[TerminalState.DELIVERED] / tot if tot else 0.0
    return deliv, cnt, tot


def verify(dt):
    plan, rows = load_plan()
    st = plan_stats(plan, rows)
    print(f"PLAN: {st['n']} windows, span={st['span']/86400:.1f}d, "
          f"duty={st['duty']:.3f}, G_max={st['gmax']/3600:.2f}h, "
          f"G_med={st['gmed']/3600:.2f}h, avg_backhaul={st['avg_bh_bps']/1e6:.2f} Mbps")
    print(f"(bundle granularity dt={dt}s)\n")
    hour, big = 3600.0, 10 * 86400.0  # 1h vs 10-day TTL
    for cls in RATE:
        for ttl, lbl in [(hour, "TTL=1h"), (big, "TTL=10d")]:
            deliv, cnt, tot = run_class(plan, cls, ttl, st['span'], dt)
            cap_ceiling = min(1.0, st['avg_bh_bps'] / (RATE[cls] * 8))
            print(f"  {cls.value:9s} {lbl:8s} deliv={deliv:6.3f}  "
                  f"ttl_exp={cnt[TerminalState.TTL_EXPIRED]:6d} "
                  f"never={cnt[TerminalState.NEVER_SCHEDULED]:6d} "
                  f"ovfl={cnt[TerminalState.QUEUE_OVERFLOW]:4d}  "
                  f"(cap_ceiling~{cap_ceiling:.2f}, n={tot})")
    return plan, st


def make_fig(outdir, dt):
    plan, st = verify(dt)
    ttls = np.array([5*60, 15*60, 30*60, 3600, 2*3600, 4*3600,
                     st['gmax'], 8*3600, 16*3600, 86400, 3*86400, 10*86400])
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    styles = {TrafficClass.EMERGENCY:("#c0392b","o"), TrafficClass.TELEMETRY:("#2c7fb8","s"),
              TrafficClass.SCIENCE_BULK:("#2e7d32","^"), TrafficClass.MEDIA:("#8e44ad","D")}
    for cls in RATE:
        ys = [run_class(plan, cls, float(t), st['span'], dt)[0] for t in ttls]
        c, m = styles[cls]
        ax.plot(ttls/3600, ys, marker=m, color=c, label=cls.value, lw=1.6, ms=4)
    ax.axvline(st['gmax']/3600, ls=":", color="gray", lw=1)
    ax.text(st['gmax']/3600, 0.02, f"$G_{{\\max}}$={st['gmax']/3600:.1f}h",
            rotation=90, fontsize=6.5, ha="right", va="bottom")
    ax.set_xscale("log")
    ax.set_xlabel("bundle lifetime TTL (h)"); ax.set_ylabel("delivery ratio")
    ax.set_ylim(0, 1.05); ax.set_title("Traffic admissibility (real LCRNS plan)", fontsize=9)
    ax.legend(fontsize=7, loc="center right"); ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, "admissibility.pdf")
    fig.savefig(p, bbox_inches="tight"); fig.savefig(p.replace(".pdf",".png"), dpi=150, bbox_inches="tight")
    print("\nwrote", p)


def fig_videosweep(outdir, dt):
    """Central figure: MEDIA delivery vs TTL for a sweep of video bitrates,
    showing the admissible-rate threshold at C_bh,eff."""
    plan, rows = load_plan(); st = plan_stats(plan, rows)
    bh = st['avg_bh_bps'] / 1e6
    print(f"C_bh,eff = {bh:.2f} Mbps; admissible iff video rate <= C_bh,eff")
    ttls = np.array([5*60, 15*60, 30*60, 3600, 2*3600, st['gmax'],
                     8*3600, 16*3600, 86400, 3*86400, 10*86400])
    mbps_list = [2, 4, 6, 8, 10, 12]
    cmap = plt.cm.viridis(np.linspace(0, 0.9, len(mbps_list)))
    fig, ax = plt.subplots(figsize=(4.8, 3.1))
    for mbps, col in zip(mbps_list, cmap):
        rate = mbps * 1e6 / 8.0
        ys = [run_class(plan, TrafficClass.MEDIA, float(t), st['span'], dt,
                        rate_override=rate)[0] for t in ttls]
        adm = "" if mbps <= bh else "  (inadmissible)"
        ax.plot(ttls/3600, ys, marker="o", ms=3.5, color=col, lw=1.6,
                label=f"{mbps} Mbps{adm}")
        print(f"  video {mbps:2d} Mbps: plateau(10d)={ys[-1]:.3f}  ceiling~{min(1,bh/mbps):.2f}")
    ax.axvline(st['gmax']/3600, ls=":", color="gray", lw=1)
    ax.text(st['gmax']/3600, 0.03, f"$G_{{\\max}}$={st['gmax']/3600:.1f}h",
            rotation=90, fontsize=6.5, ha="right", va="bottom")
    ax.axhline(1.0, ls="--", color="k", lw=0.6)
    ax.set_xscale("log"); ax.set_xlabel("bundle lifetime TTL (h)")
    ax.set_ylabel("video delivery ratio"); ax.set_ylim(0, 1.05)
    ax.set_title(f"Admissible video rate ($C_{{bh,eff}}\\approx{bh:.1f}$ Mbps)", fontsize=9)
    ax.legend(fontsize=6.3, loc="center right", title="video feed", title_fontsize=6.5)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout(); os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, "admissibility.pdf")
    fig.savefig(p, bbox_inches="tight"); fig.savefig(p.replace(".pdf",".png"), dpi=150, bbox_inches="tight")
    print("wrote", p)


def closed_form_P(plan, st, L, rate_bps):
    """Admissibility law in closed form: the gap-limited fraction
    [sum(contact) + sum_gaps min(L, g)] / span, capped by the capacity share
    C_bh,eff / R. No simulation."""
    W = plan._windows
    gaps = [W[i + 1].start_ts - W[i].end_ts for i in range(len(W) - 1)]
    p_gap = (st['contact'] + sum(min(L, g) for g in gaps)) / st['span']
    return min(p_gap, st['avg_bh_bps'] / rate_bps)


def fig_videosweep_closed(outdir, dt, check=True):
    plan, rows = load_plan(); st = plan_stats(plan, rows)
    bh = st['avg_bh_bps'] / 1e6
    ttls = np.geomspace(5 * 60, 10 * 86400, 60)
    mbps_list = [2, 4, 6, 8, 10, 12]
    cmap = plt.cm.viridis(np.linspace(0, 0.9, len(mbps_list)))
    if check:   # closed form vs the gateway scheduler, all three regimes
        for mbps, L in [(4, 3600), (4, 3 * 86400), (12, 3600), (12, 3 * 86400), (10, 2 * 3600)]:
            r = mbps * 1e6 / 8.0
            sim = run_class(plan, TrafficClass.MEDIA, float(L), st['span'], dt, rate_override=r)[0]
            cf = closed_form_P(plan, st, L, mbps * 1e6)
            print(f"  check {mbps:2d} Mbps L={L/3600:5.1f} h: closed={cf:.3f} scheduler={sim:.3f} |d|={abs(cf-sim):.3f}")
    fig, ax = plt.subplots(figsize=(4.8, 3.1))
    for mbps, col in zip(mbps_list, cmap):
        ys = [closed_form_P(plan, st, L, mbps * 1e6) for L in ttls]
        adm = "" if mbps <= bh else "  (inadmissible)"
        ax.plot(ttls / 3600, ys, color=col, lw=1.7, label=f"{mbps} Mbps{adm}")
    ax.axvline(st['gmax'] / 3600, ls=":", color="gray", lw=1)
    ax.text(st['gmax'] / 3600, 0.03, f"$G_{{\\max}}$={st['gmax']/3600:.1f}h",
            rotation=90, fontsize=6.5, ha="right", va="bottom")
    ax.axhline(1.0, ls="--", color="k", lw=0.6)
    ax.set_xscale("log"); ax.set_xlabel("bundle lifetime $L$ (h)")
    ax.set_ylabel("video delivery ratio"); ax.set_ylim(0, 1.05)
    ax.set_title(f"Admissible video rate ($C_{{bh,eff}}\\approx{bh:.1f}$ Mbps)", fontsize=9)
    ax.legend(fontsize=6.3, loc="center right", title="video feed", title_fontsize=6.5)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout(); os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, "admissibility.pdf")
    fig.savefig(p, bbox_inches="tight"); fig.savefig(p.replace(".pdf", ".png"), dpi=150, bbox_inches="tight")
    print("wrote", p, f"(closed form; C_bh,eff={bh:.2f} Mbps)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--closed", action="store_true", help="closed-form admissibility figure")
    ap.add_argument("--outdir", default="figs")
    ap.add_argument("--dt", type=float, default=300.0, help="bundle granularity (s)")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--sweep", action="store_true", help="video-bitrate sweep (central fig)")
    a = ap.parse_args()
    if a.closed: fig_videosweep_closed(a.outdir, a.dt)
    elif a.verify: verify(a.dt)
    elif a.sweep: fig_videosweep(a.outdir, a.dt)
    else: make_fig(a.outdir, a.dt)
