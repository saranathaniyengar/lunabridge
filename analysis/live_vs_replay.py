"""
analysis/live_vs_replay.py

Cross-check of the LIVE gateway scheduler (link_controller/live_gateway.py,
behind the N6 -> BPv7 bridge) against the offline scheduler
(gateway/scheduler.py) replayed on the very same live arrivals: the live run's
sent.jsonl (arrival time, class, size), the same compressed contacts, rate,
buffer, lifetimes, drain policy, and admission rule. The offline scheduler
admits and drains per window, so each contact is split into 1 s sub-windows to
match the live, paced drain.

Writes LaTeX rows for the paper table (per configuration and class: live
delivery, offline replay delivery) and prints the fates.

Usage: python3 analysis/live_vs_replay.py [--runs runs/gateway_live] [--out rows.tex]
"""
import argparse, copy, gzip, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gateway.contact_plan import ContactPlan, ContactWindow          # noqa: E402
from gateway.scheduler import Scheduler, SchedulingPolicy           # noqa: E402
from gateway.telemetry import BundleRecord, TerminalState           # noqa: E402
from gateway.traffic import TrafficClass                            # noqa: E402

CLS = {"emergency": TrafficClass.EMERGENCY, "telemetry": TrafficClass.TELEMETRY,
       "sci_bulk": TrafficClass.SCIENCE_BULK, "media": TrafficClass.MEDIA}
ORDER = ["emergency", "telemetry", "sci_bulk", "media"]
POL = {"fifo": SchedulingPolicy.FIFO, "strict": SchedulingPolicy.STRICT_PRIORITY,
       "wfq": SchedulingPolicy.WFQ}
LABEL = {"fifo_droptail": ("FIFO", "drop-tail"), "fifo_priority": ("FIFO", "priority-aware"),
         "strict_droptail": ("strict", "drop-tail"), "strict_priority": ("strict", "priority-aware"),
         "wfq_droptail": ("WFQ", "drop-tail"), "wfq_priority": ("WFQ", "priority-aware")}


def opener(p):
    return gzip.open(p + ".gz", "rt") if os.path.exists(p + ".gz") else open(p)


def run_dir(d):
    cfg = os.path.basename(d.rstrip("/"))
    policy, admission = cfg.split("_")
    sched = json.load(open(os.path.join(d, "schedule.json")))
    log = open(os.path.join(d, "run.log")).read()
    import re
    m = re.search(r"buffer=(\d+) B rate=(\d+) b/s", log)
    if m is None:
        raise RuntimeError(f"{d}: run did not reach the gateway (see run.log)")
    buf, rate = int(m.group(1)), float(m.group(2))
    sent = [json.loads(l) for l in opener(os.path.join(d, "sent.jsonl"))]
    got = {int(json.loads(l)["h"].split(":")[0]) for l in opener(os.path.join(d, "recv.jsonl"))}
    live = {c: (sum(s["seq"] in got for s in sent if s["cls"] == c),
                sum(1 for s in sent if s["cls"] == c)) for c in ORDER}
    # live losses: dropped / evicted at admission or expired in the buffer
    lost_seq = {}
    for l in opener(os.path.join(d, "gw.jsonl")):
        e = json.loads(l)
        if e["ev"] in ("drop", "evict", "expire"):
            lost_seq[e["seq"]] = e["cls"]
    live_lost = {c: sum(1 for v in lost_seq.values() if v == c) for c in ORDER}
    # offline replay on the same arrivals, 1 s sub-windows inside every contact
    t0 = sched["t_start_unix"]
    wins = []
    for s, e in sched["contacts_unix"]:
        t = s
        while t < e:
            wins.append(ContactWindow(f"w{len(wins)}", t - t0, min(t + 1.0, e) - t0, rate))
            t += 1.0
    recs = []
    for s in sent:
        size = s.get("size", s["n"] + 60)        # same bytes the live gateway charged
        b = BundleRecord(str(s["seq"]), s["cls"], CLS[s["cls"]], size, ingress_ts=s["t"] - t0)
        b.set_ttl(sched["lifetimes"][s["cls"]])
        recs.append(b)
    Scheduler(ContactPlan(wins), max_queue_bytes=buf, policy=POL[policy],
              priority_aware_admission=(admission == "priority"),
              fifo_skip_over=False).run(recs)     # strict FIFO, like the paced live link
    off = {c: (sum(1 for b in recs if b.flow_id == c and b.terminal_state is TerminalState.DELIVERED),
               sum(1 for b in recs if b.flow_id == c)) for c in ORDER}
    off_lost = {c: sum(1 for b in recs if b.flow_id == c and b.terminal_state in
                       (TerminalState.QUEUE_OVERFLOW, TerminalState.TTL_EXPIRED)) for c in ORDER}
    return cfg, live, off, live_lost, off_lost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs/gateway_live")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lines = []
    for cfg in LABEL:
        d = os.path.join(a.runs, cfg)
        if not os.path.isdir(d):
            print("missing", d); continue
        cfg, live, off, live_lost, off_lost = run_dir(d)
        print(cfg, {c: (f"live {live[c][0]}/{live[c][1]} = {live[c][0] / live[c][1]:.3f}",
                        f"replay {off[c][0] / off[c][1]:.3f}") for c in ORDER})
        dr, ad = LABEL[cfg]
        print("   lost live", {c: round(live_lost[c] / live[c][1], 3) for c in ORDER},
              "replay", {c: round(off_lost[c] / off[c][1], 3) for c in ORDER})
        dmax = max(abs(live[c][0] / live[c][1] - off[c][0] / off[c][1]) for c in ORDER)
        lines.append(f"{dr} & {ad} & " + " & ".join(
            f"{100 * live[c][0] / live[c][1]:.0f} ({100 * live_lost[c] / live[c][1]:.0f})"
            for c in ORDER) + f" & {int(100 * dmax + 0.5 + 1e-9)} \\\\")      # round half up (7.5 -> 8)
    if a.out:
        open(a.out, "w").write("\n".join(lines) + "\n")
        print("wrote", a.out)


if __name__ == "__main__":
    main()
