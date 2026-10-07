#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
"""
link_controller/ceiling_pilot.py

Measure the lifetime-to-gap delivery law on REAL DTN nodes, over a
time-compressed slice of the real LCRNS 1-SV contact plan.

Setup (all real software, nothing simulated):
  * two network namespaces joined by a veth shaped with tc netem
    (one-way light time 58 ms, 10 Mbit/s, 100 % loss during gaps);
  * one relay µD3TN node and one moon µD3TN node per lifetime value L
    (BPv7 lifetime is set per node with `ud3tn -l`; AAP cannot set it per
    bundle in v0.15.0);
  * every moon node gets the SAME scheduled contacts towards the relay
    (absolute DTN times from a common t0), so store-carry-forward and
    lifetime expiry happen inside µD3TN, not in a model.

Time compression: every plan time and every lifetime is divided by k. The
law depends only on dimensionless ratios (rho = L/G, duty cycle), which are
invariant under this scaling. The one-way light time is kept physical (58 ms);
it is negligible against compressed gaps of tens of seconds.

A bundle is injected from each moon node every --dt seconds between the first
contact start and the last contact end. Measured delivery per L is compared with
the closed form  P(L) = [sum(contact) + sum_gaps min(L, g)] / span  evaluated on
the configured integer schedule, and with a per-bundle prediction from the
actual send timestamps.

Usage (on the rack, as root):
  sudo python3 ceiling_pilot.py run --plan lcrns_relay_contact_plan_1sv.csv \
       --windows 8 --k 720 --lifetimes 2,5,9,14,19,24,28,36 --out ~/ceiling_pilot
"""
from __future__ import annotations
import argparse, csv, json, os, signal, subprocess, sys, threading, time

UNIX_TO_DTN = 946684800  # seconds between 1970-01-01 and 2000-01-01 (DTN epoch)
NS_M, NS_R = "cp_moon", "cp_relay"
VE_M, VE_R = "cpveth_m", "cpveth_r"
IP_M, IP_R = "10.21.0.1", "10.21.0.2"
RELAY_AAP, RELAY_MTCP = 4242, 4224
MOON_AAP0, MOON_MTCP0 = 4300, 4400
OWLT_MS, RATE = 58, "10mbit"
NETEM_GUARD_S = 0.5   # open the physical link slightly before / close slightly
                      # after the DTN contact, so TCP SYN loss at the boundary
                      # does not bias the measurement; µD3TN only sends inside
                      # its scheduled contact anyway.
USER_SITE = "/home/nuwins-rack-2/.local/lib/python3.10/site-packages"
UD = "/home/nuwins-rack-2/ud3tn/build/posix/ud3tn"


def sh(cmd, check=True, quiet=True):
    return subprocess.run(cmd, shell=True, check=check,
                          stdout=subprocess.DEVNULL if quiet else None,
                          stderr=subprocess.DEVNULL if quiet else None)


# --------------------------------------------------------------------------- #
# schedule
# --------------------------------------------------------------------------- #
def compressed_windows(plan_csv, n, k):
    rows = list(csv.DictReader(open(plan_csv)))[:n]
    s0 = float(rows[0]["start_sec"])
    return [(round((float(r["start_sec"]) - s0) / k),
             round((float(r["end_sec"]) - s0) / k)) for r in rows]


def closed_form(windows, L):
    span = windows[-1][1] - windows[0][0]
    cont = sum(e - s for s, e in windows)
    gaps = [windows[i + 1][0] - windows[i][1] for i in range(len(windows) - 1)]
    return (cont + sum(min(L, g) for g in gaps)) / span


def predict_one(t, contacts, L):
    """Deliverable iff sent inside a contact, or the next contact opens before
    the bundle's lifetime runs out (times in unix seconds)."""
    for s, e in contacts:
        if s <= t < e:
            return True
        if t < s:
            # strict: a bundle with zero lifetime left when the contact opens
            # cannot be forwarded (contact set-up takes non-zero time)
            return (s - t) < L
    return False


# --------------------------------------------------------------------------- #
# in-namespace workers (run with PYTHONPATH=USER_SITE)
# --------------------------------------------------------------------------- #
def worker_rx(args):
    from ud3tn_utils.aap import AAPTCPClient
    out = open(args.file, "a", buffering=1)
    with AAPTCPClient(address=("127.0.0.1", RELAY_AAP)) as c:
        c.register("sink")
        while True:
            msg = c.receive()
            pl = getattr(msg, "payload", None)
            if pl:
                out.write(json.dumps({"p": pl.decode(errors="replace"),
                                      "t": time.time()}) + "\n")


def worker_moon(args):
    from ud3tn_utils.aap import AAPTCPClient
    from ud3tn_utils.config import LegacyConfigMessage, Contact
    sched = json.load(open(args.schedule))
    Ls = sched["lifetimes"]
    contacts = [Contact(s, e, 10_000_000, None) if Contact.__init__.__code__.co_argcount > 4
                else Contact(s, e, 10_000_000) for s, e in sched["contacts_dtn"]]
    # 1) configure the identical contact plan on every moon node
    for i in range(len(Ls)):
        with AAPTCPClient(address=("127.0.0.1", MOON_AAP0 + i)) as c:
            c.register()
            msg = bytes(LegacyConfigMessage("dtn://relay.dtn/",
                                            f"mtcp:{IP_R}:{RELAY_MTCP}",
                                            contacts=contacts))
            c.send_bundle(f"dtn://moon{i}.dtn/config", msg)
    print("[moon] contacts configured on", len(Ls), "nodes", flush=True)
    # 2) persistent sender per node
    clients = []
    for i in range(len(Ls)):
        c = AAPTCPClient(address=("127.0.0.1", MOON_AAP0 + i))
        c.connect(); c.register("src")
        clients.append(c)
    t_start, t_end, dt = sched["t_start_unix"], sched["t_end_unix"], sched["dt"]
    while time.time() < t_start:
        time.sleep(0.01)
    out = open(args.file, "a", buffering=1)
    seq, t_next = 0, t_start
    while t_next < t_end:
        while time.time() < t_next:
            time.sleep(0.002)
        now = time.time()
        for i, c in enumerate(clients):
            c.send_bundle("dtn://relay.dtn/sink", f"{i}:{seq}:{now:.3f}".encode())
            out.write(json.dumps({"i": i, "seq": seq, "t": now}) + "\n")
        seq += 1; t_next += dt
    for c in clients:
        c.disconnect()
    print(f"[moon] injected {seq} rounds x {len(Ls)} nodes", flush=True)


# --------------------------------------------------------------------------- #
# orchestration (root, default namespace)
# --------------------------------------------------------------------------- #
def netem(ns, dev, loss):
    sh(f"ip netns exec {ns} tc qdisc change dev {dev} root netem "
       f"delay {OWLT_MS}ms rate {RATE}" + (" loss 100%" if loss else ""), check=False)


def cleanup():
    for ns in (NS_M, NS_R):
        sh(f"ip netns pids {ns} 2>/dev/null | xargs -r kill", check=False)
    time.sleep(0.5)
    for ns in (NS_M, NS_R):
        sh(f"ip netns del {ns}", check=False)


def run(args):
    os.makedirs(args.out, exist_ok=True)
    Ls = [int(x) for x in args.lifetimes.split(",")]
    W = compressed_windows(args.plan, args.windows, args.k)
    cleanup()
    # --- link
    sh(f"ip netns add {NS_M}"); sh(f"ip netns add {NS_R}")
    sh(f"ip link add {VE_M} type veth peer name {VE_R}")
    sh(f"ip link set {VE_M} netns {NS_M}"); sh(f"ip link set {VE_R} netns {NS_R}")
    for ns, dev, ip in ((NS_M, VE_M, IP_M), (NS_R, VE_R, IP_R)):
        sh(f"ip netns exec {ns} ip addr add {ip}/24 dev {dev}")
        sh(f"ip netns exec {ns} ip link set {dev} up")
        sh(f"ip netns exec {ns} ip link set lo up")
        sh(f"ip netns exec {ns} tc qdisc add dev {dev} root netem "
           f"delay {OWLT_MS}ms rate {RATE} loss 100%")      # start dark
    # --- DTN nodes
    logs = []
    lr = open(os.path.join(args.out, "relay.log"), "w"); logs.append(lr)
    subprocess.Popen(f"ip netns exec {NS_R} {UD} -e dtn://relay.dtn/ -l 3600 "
                     f"-a 127.0.0.1 -p {RELAY_AAP} -c mtcp:{IP_R},{RELAY_MTCP} -L 3",
                     shell=True, stdout=lr, stderr=lr)
    for i, L in enumerate(Ls):
        lm = open(os.path.join(args.out, f"moon{i}_L{L}.log"), "w"); logs.append(lm)
        subprocess.Popen(f"ip netns exec {NS_M} {UD} -e dtn://moon{i}.dtn/ -l {L} "
                         f"-a 127.0.0.1 -p {MOON_AAP0 + i} "
                         f"-c mtcp:{IP_M},{MOON_MTCP0 + i} -L 3",
                         shell=True, stdout=lm, stderr=lm)
    time.sleep(2.0)
    env = f"env PYTHONPATH={USER_SITE}"
    me = os.path.abspath(__file__)
    rxf, txf = os.path.join(args.out, "recv.jsonl"), os.path.join(args.out, "sent.jsonl")
    for f in (rxf, txf):
        open(f, "w").close()
    rx = subprocess.Popen(f"ip netns exec {NS_R} {env} python3 {me} rx --file {rxf}",
                          shell=True, preexec_fn=os.setsid)
    # --- schedule from a common integer t0
    t0_dtn = int(time.time() - UNIX_TO_DTN) + args.lead
    contacts_dtn = [(t0_dtn + s, t0_dtn + e) for s, e in W]
    contacts_unix = [(s + UNIX_TO_DTN, e + UNIX_TO_DTN) for s, e in contacts_dtn]
    sched = dict(lifetimes=Ls, k=args.k, windows_rel=W, contacts_dtn=contacts_dtn,
                 contacts_unix=contacts_unix, t_start_unix=contacts_unix[0][0],
                 t_end_unix=contacts_unix[-1][1], dt=args.dt)
    sf = os.path.join(args.out, "schedule.json"); json.dump(sched, open(sf, "w"), indent=1)
    print(f"[run] {len(W)} windows, k={args.k}, span={W[-1][1]-W[0][0]} s, "
          f"lifetimes={Ls}; starts in {args.lead} s", flush=True)

    # --- physical blackout follows the plan (with guard band)
    def toggler():
        for s, e in contacts_unix:
            while time.time() < s - NETEM_GUARD_S: time.sleep(0.01)
            netem(NS_M, VE_M, False); netem(NS_R, VE_R, False)
            while time.time() < e + NETEM_GUARD_S: time.sleep(0.01)
            netem(NS_M, VE_M, True); netem(NS_R, VE_R, True)
    threading.Thread(target=toggler, daemon=True).start()

    moon = subprocess.run(f"ip netns exec {NS_M} {env} python3 {me} moon "
                          f"--schedule {sf} --file {txf}", shell=True)
    time.sleep(args.drain)
    os.killpg(os.getpgid(rx.pid), signal.SIGTERM)
    cleanup()
    analyze(args.out)


def analyze(out):
    sched = json.load(open(os.path.join(out, "schedule.json")))
    Ls, W, C = sched["lifetimes"], sched["windows_rel"], sched["contacts_unix"]
    sent = [json.loads(l) for l in open(os.path.join(out, "sent.jsonl"))]
    recv = set()
    for l in open(os.path.join(out, "recv.jsonl")):
        p = json.loads(l)["p"].split(":")
        recv.add((int(p[0]), int(p[1])))
    rows = []
    for i, L in enumerate(Ls):
        mine = [s for s in sent if s["i"] == i]
        n = len(mine)
        got = sum((i, s["seq"]) in recv for s in mine)
        pred = sum(predict_one(s["t"], C, L) for s in mine)
        Gmax = max(W[j + 1][0] - W[j][1] for j in range(len(W) - 1))
        rows.append(dict(L=L, rho=L / Gmax, sent=n, delivered=got,
                         measured=got / n if n else float("nan"),
                         predicted=pred / n if n else float("nan"),
                         closed_form=closed_form(W, L)))
    # mismatch diagnostics: bundles the closed form says deliverable but µD3TN
    # did not deliver -- report their slack (time left when the contact opened)
    miss = []
    for i, L in enumerate(Ls):
        for s in sent:
            if s["i"] != i or (i, s["seq"]) in recv or not predict_one(s["t"], C, L):
                continue
            nxt = next((cs for cs, ce in C if cs > s["t"]), None)
            inside = any(cs <= s["t"] < ce for cs, ce in C)
            miss.append(dict(L=L, inside=inside,
                             slack=None if inside or nxt is None else s["t"] + L - nxt))
    if miss:
        gap_sl = sorted(m["slack"] for m in miss if m["slack"] is not None)
        print(f"\n[diag] {len(miss)} predicted-but-undelivered bundles: "
              f"{sum(m['inside'] for m in miss)} sent inside a contact, "
              f"{len(gap_sl)} sent in a gap with slack at contact start "
              f"min={min(gap_sl, default=float('nan')):.2f}s "
              f"max={max(gap_sl, default=float('nan')):.2f}s")
    for r in rows:
        r["missed_with_slack_s"] = [round(m["slack"], 3) for m in miss
                                    if m["L"] == r["L"] and m["slack"] is not None]
    json.dump(rows, open(os.path.join(out, "results.json"), "w"), indent=1)
    span = W[-1][1] - W[0][0]
    duty = sum(e - s for s, e in W) / span
    print(f"\nduty cycle delta = {duty:.3f}")
    print(f"{'L(s)':>5} {'rho':>5} {'sent':>5} {'deliv':>6} {'measured':>9} "
          f"{'per-bundle':>10} {'closed-form':>11}")
    for r in rows:
        print(f"{r['L']:>5} {r['rho']:>5.2f} {r['sent']:>5} {r['delivered']:>6} "
              f"{r['measured']:>9.3f} {r['predicted']:>10.3f} {r['closed_form']:>11.3f}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--plan", required=True); r.add_argument("--windows", type=int, default=8)
    r.add_argument("--k", type=float, default=720); r.add_argument("--lifetimes", required=True)
    r.add_argument("--dt", type=float, default=1.0); r.add_argument("--lead", type=int, default=15)
    r.add_argument("--drain", type=float, default=5.0); r.add_argument("--out", required=True)
    x = sub.add_parser("rx"); x.add_argument("--file", required=True)
    m = sub.add_parser("moon"); m.add_argument("--schedule", required=True)
    m.add_argument("--file", required=True)
    a = sub.add_parser("analyze"); a.add_argument("--out", required=True)
    args = ap.parse_args()
    {"run": run, "rx": worker_rx, "moon": worker_moon,
     "analyze": lambda a: analyze(a.out)}[args.cmd](args)


if __name__ == "__main__":
    main()
