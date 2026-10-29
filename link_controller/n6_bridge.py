#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
"""
link_controller/n6_bridge.py

LIVE N6 -> BPv7 bridge: every uplink packet that leaves the 5G user plane is
encapsulated, as it arrives, into a BPv7 bundle on a real uD3TN surface node,
which forwards it over the netem-shaped relay link on a time-compressed slice of
the real LCRNS contact plan (same DTN segment as ceiling_pilot.py).

Class from 5G QoS, not DSCP: the bridge taps the UPF's N3 side (GTP-U toward the
UPF), reads each packet's TEID and QFI, and maps TEID -> 5QI with the table
extracted from the NGAP PDU Session Resource Setup of that session
(n2_qos_map.py). The 5QI selects the traffic class and therefore the bundle
lifetime (one surface node per class; uD3TN v0.15 sets lifetime per node).
The inner IP packet -- exactly what the UPF forwards to N6 -- is the payload.

Usage (root on the 5G host):
  tcpdump -i eth0 -U -w - 'udp dst port 2152 and dst host <UPF N3 IP>' (in UPF netns) |
  python3 n6_bridge.py run --plan <1sv.csv> --windows 8 --k 720 --qos-map map.json --out DIR
"""
from __future__ import annotations
import argparse, csv, json, os, signal, struct, subprocess, sys, threading, time

UNIX_TO_DTN = 946684800
NS_S, NS_R = "lb_surf", "lb_relay"
VE_S, VE_R = "lbveth_s", "lbveth_r"
IP_S, IP_R = "10.22.0.1", "10.22.0.2"
RELAY_AAP, RELAY_MTCP = 4642, 4624
SURF_AAP0, SURF_MTCP0 = 4700, 4800
OWLT_MS, RATE = 58, "10mbit"
NETEM_GUARD_S = 0.5
HERE = os.path.dirname(os.path.abspath(__file__))
UD = os.environ.get("UD3TN", os.path.expanduser("~nuwins-server1/lb_bridge/ud3tn_bin"))
PY = os.environ.get("BRIDGE_PY", sys.executable)
UD_LOG = os.environ.get("UD_LOG", "3")

# 5QI -> class, and class lifetimes (h) from the boundary law (Table II)
FIVEQI_CLASS = {69: "emergency", 70: "telemetry", 9: "sci_bulk", 7: "media"}
LIFETIME_H = {"emergency": 6.563, "telemetry": 13.126, "sci_bulk": 45.941, "media": 3.281}
CLASSES = ["emergency", "telemetry", "sci_bulk", "media"]


def sh(cmd, check=True):
    return subprocess.run(cmd, shell=True, check=check,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def compressed_windows(plan_csv, n, k):
    rows = list(csv.DictReader(open(plan_csv)))[:n]
    s0 = float(rows[0]["start_sec"])
    return [(round((float(r["start_sec"]) - s0) / k),
             round((float(r["end_sec"]) - s0) / k)) for r in rows]


def closed_form(W, L):
    span = W[-1][1] - W[0][0]
    gaps = [W[i + 1][0] - W[i][1] for i in range(len(W) - 1)]
    return (sum(e - s for s, e in W) + sum(min(L, g) for g in gaps)) / span


def predict_one(t, contacts, L):
    for s, e in contacts:
        if s <= t < e:
            return True
        if t < s:
            return (s - t) < L
    return False


# --------------------------------------------------------------------------- #
# GTP-U parsing (pcap stream, Ethernet link type)
# --------------------------------------------------------------------------- #
def gtpu_packets(stream):
    """Yield (ts, teid, qfi, inner_ip_bytes) from a libpcap byte stream."""
    gh = stream.read(24)
    if len(gh) < 24:
        return
    magic = struct.unpack("<I", gh[:4])[0]
    endian = "<" if magic in (0xA1B2C3D4, 0xA1B23C4D) else ">"
    nano = magic in (0xA1B23C4D, 0x4D3CB2A1)
    linktype = struct.unpack(endian + "I", gh[20:24])[0]
    l2 = {1: 14, 113: 16, 276: 20}.get(linktype, 14)
    while True:
        ph = stream.read(16)
        if len(ph) < 16:
            return
        sec, frac, incl, _ = struct.unpack(endian + "IIII", ph)
        pkt = stream.read(incl)
        ts = sec + frac / (1e9 if nano else 1e6)
        ip = pkt[l2:]
        if len(ip) < 28 or ip[0] >> 4 != 4 or ip[9] != 17:
            continue
        ihl = (ip[0] & 0x0F) * 4
        udp = ip[ihl:]
        g = udp[8:]
        if len(g) < 8 or g[1] != 0xFF:          # G-PDU only
            continue
        flags, teid = g[0], struct.unpack("!I", g[4:8])[0]
        off, qfi = 8, None
        if flags & 0x07:                          # E/S/PN present: 4 more bytes
            nxt = g[11]; off = 12
            while nxt:
                ln = g[off] * 4
                if nxt == 0x85 and ln >= 4:       # PDU session container
                    qfi = g[off + 2] & 0x3F
                nxt = g[off + ln - 1]; off += ln
        yield ts, teid, qfi, bytes(g[off:])


# --------------------------------------------------------------------------- #
# in-namespace workers
# --------------------------------------------------------------------------- #
def aap_connect(port, register=None, tries=60):
    """Connect to a uD3TN AAP port, waiting for the node to come up."""
    from ud3tn_utils.aap import AAPTCPClient
    for i in range(tries):
        try:
            c = AAPTCPClient(address=("127.0.0.1", port)); c.connect()
            if register is not None:
                c.register(register) if register else c.register()
            return c
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"uD3TN AAP port {port} not reachable")


def configure_contacts(i, contacts, LegacyConfigMessage):
    c = aap_connect(SURF_AAP0 + i, register="")
    c.send_bundle(f"dtn://surf{i}.dtn/config",
                  bytes(LegacyConfigMessage("dtn://relay.dtn/", f"mtcp:{IP_R}:{RELAY_MTCP}",
                                            contacts=contacts)))
    c.disconnect()


def worker_rx(args):
    out = open(args.file, "a", buffering=1)
    c = aap_connect(RELAY_AAP, register="sink")
    while True:
        msg = c.receive()
        pl = getattr(msg, "payload", None)
        if pl:
            hdr = pl[:pl.index(b"|")].decode()
            out.write(json.dumps({"h": hdr, "n": len(pl), "t": time.time()}) + "\n")


def worker_bridge(args):
    """Read GTP-U pcap from stdin; classify by TEID->5QI; send as bundles."""
    from ud3tn_utils.aap import AAPTCPClient
    from ud3tn_utils.config import LegacyConfigMessage, Contact
    sched = json.load(open(args.schedule))
    qmap = {int(k): int(v) for k, v in json.load(open(args.qos_map)).items()}
    contacts = [Contact(s, e, 10_000_000) for s, e in sched["contacts_dtn"]]
    clients = {}
    for i, cls in enumerate(CLASSES):
        configure_contacts(i, contacts, LegacyConfigMessage)
        clients[cls] = aap_connect(SURF_AAP0 + i, register="src")
    print("[bridge] contacts configured; qos map", qmap, flush=True)
    out = open(args.file, "a", buffering=1)
    t0, t1 = sched["t_start_unix"], sched["t_end_unix"]
    seq, unknown = 0, 0
    for ts, teid, qfi, inner in gtpu_packets(sys.stdin.buffer):
        now = time.time()
        if now >= t1:
            break
        if now < t0:
            continue
        fiveqi = qmap.get(teid)
        cls = FIVEQI_CLASS.get(fiveqi)
        if cls is None:
            unknown += 1
            continue
        hdr = f"{seq}:{cls}:{teid}:{qfi}:{fiveqi}:{now:.3f}".encode()
        clients[cls].send_bundle("dtn://relay.dtn/sink", hdr + b"|" + inner)
        out.write(json.dumps({"seq": seq, "cls": cls, "teid": teid, "qfi": qfi,
                              "5qi": fiveqi, "n": len(inner), "t": now,
                              "t_cap": ts}) + "\n")
        seq += 1
    print(f"[bridge] encapsulated {seq} packets; {unknown} with unknown TEID", flush=True)
    for c in clients.values():
        c.disconnect()


def worker_gateway(args):
    """Bridge + LIVE gateway scheduler: the bridge thread classifies each packet
    by TEID->5QI and admits it into a bounded buffer; this thread drains the
    buffer during contacts at the contact rate, in policy order, into the uD3TN
    surface node of the bundle's class."""
    sys.path.insert(0, HERE)
    from live_gateway import LiveGateway
    from ud3tn_utils.aap import AAPTCPClient
    from ud3tn_utils.config import LegacyConfigMessage, Contact
    sched = json.load(open(args.schedule))
    qmap = {int(k): int(v) for k, v in json.load(open(args.qos_map)).items()}
    contacts = [Contact(s, e, 10_000_000) for s, e in sched["contacts_dtn"]]
    C = sched["contacts_unix"]
    clients = {}
    for i, cls in enumerate(CLASSES):
        configure_contacts(i, contacts, LegacyConfigMessage)
        clients[cls] = aap_connect(SURF_AAP0 + i, register="src")
    out = open(args.file, "a", buffering=1)
    evf = open(args.file.replace("sent.jsonl", "gw.jsonl"), "w", buffering=1)
    lock = threading.Lock()

    def log(ev, b, t):
        with lock:
            evf.write(json.dumps({"ev": ev, "seq": b["seq"], "cls": b["cls"], "t": t}) + "\n")
    gw = LiveGateway(args.policy, args.admission, int(args.buffer_bytes),
                     sched["lifetimes"], log=log)
    print(f"[gateway] policy={args.policy} admission={args.admission} "
          f"buffer={int(args.buffer_bytes)} B rate={args.rate_bps:.0f} b/s; qos map {qmap}",
          flush=True)
    t0, t1 = sched["t_start_unix"], sched["t_end_unix"]
    state = {"seq": 0, "unknown": 0, "done": False}

    def reader():
        for ts, teid, qfi, inner in gtpu_packets(sys.stdin.buffer):
            now = time.time()
            if now >= t1:
                break
            if now < t0:
                continue
            fiveqi = qmap.get(teid)
            cls = FIVEQI_CLASS.get(fiveqi)
            if cls is None:
                state["unknown"] += 1
                continue
            seq = state["seq"]; state["seq"] += 1
            hdr = f"{seq}:{cls}:{teid}:{qfi}:{fiveqi}:{now:.3f}".encode()
            payload = hdr + b"|" + inner
            # one size for buffer and link: bridge header + inner packet + 60 B
            # of BPv7/convergence-layer overhead (the replay uses the same)
            size = len(payload) + 60
            b = {"seq": seq, "cls": cls, "n": size, "t": now, "payload": payload}
            with lock:
                out.write(json.dumps({"seq": seq, "cls": cls, "teid": teid, "qfi": qfi,
                                      "5qi": fiveqi, "n": len(inner), "size": size,
                                      "t": now, "t_cap": ts}) + "\n")
            gw.admit(b, now)
        state["done"] = True
    threading.Thread(target=reader, daemon=True).start()

    def in_contact(t):
        return any(s <= t < e for s, e in C)
    sent_fwd = 0
    t_free = 0.0                      # paced link: next instant the link is free
    while time.time() < t1:
        now = time.time()
        if not in_contact(now):
            time.sleep(0.005); continue
        if now < t_free:
            time.sleep(min(0.005, t_free - now)); continue
        b = gw.next(now)
        if b is None:
            time.sleep(0.002); continue
        clients[b["cls"]].send_bundle("dtn://relay.dtn/sink", b["payload"])
        log("fwd", b, now)
        sent_fwd += 1
        t_free = max(t_free, now) + b["n"] * 8 / args.rate_bps
    left = gw.snapshot()
    for c, bs in left.items():
        for b in bs:
            log("queued_at_end", b, time.time())
    print(f"[gateway] admitted+forwarded {sent_fwd}; encapsulated {state['seq']}; "
          f"{state['unknown']} unknown TEID; queued at end "
          f"{ {c: len(v) for c, v in left.items()} }", flush=True)
    for c in clients.values():
        c.disconnect()
    out.close(); evf.close()
    os._exit(0)   # the reader thread is blocked on stdin; skip interpreter teardown


# --------------------------------------------------------------------------- #
# orchestration (root)
# --------------------------------------------------------------------------- #
def netem(ns, dev, loss):
    sh(f"ip netns exec {ns} tc qdisc change dev {dev} root netem "
       f"delay {OWLT_MS}ms rate {RATE}" + (" loss 100%" if loss else ""), check=False)


def cleanup():
    for ns in (NS_S, NS_R):
        sh(f"ip netns pids {ns} 2>/dev/null | xargs -r kill", check=False)
    time.sleep(0.5)
    for ns in (NS_S, NS_R):
        sh(f"ip netns del {ns}", check=False)


def run(args):
    os.makedirs(args.out, exist_ok=True)
    W = compressed_windows(args.plan, args.windows, args.k)
    Ls = {c: max(1, round(LIFETIME_H[c] * 3600 / args.k)) for c in CLASSES}
    cleanup()
    sh(f"ip netns add {NS_S}"); sh(f"ip netns add {NS_R}")
    sh(f"ip link add {VE_S} type veth peer name {VE_R}")
    sh(f"ip link set {VE_S} netns {NS_S}"); sh(f"ip link set {VE_R} netns {NS_R}")
    for ns, dev, ip in ((NS_S, VE_S, IP_S), (NS_R, VE_R, IP_R)):
        sh(f"ip netns exec {ns} ip addr add {ip}/24 dev {dev}")
        sh(f"ip netns exec {ns} ip link set {dev} up")
        sh(f"ip netns exec {ns} ip link set lo up")
        sh(f"ip netns exec {ns} tc qdisc add dev {dev} root netem "
           f"delay {OWLT_MS}ms rate {RATE} loss 100%")
    lr = open(os.path.join(args.out, "relay.log"), "w")
    subprocess.Popen(f"ip netns exec {NS_R} {UD} -e dtn://relay.dtn/ -l 86400 "
                     f"-a 127.0.0.1 -p {RELAY_AAP} -c mtcp:{IP_R},{RELAY_MTCP} -L {UD_LOG} "
                     f"-S /tmp/lb_relay.aap2.sock",
                     shell=True, stdout=lr, stderr=lr)
    for i, cls in enumerate(CLASSES):
        lm = open(os.path.join(args.out, f"surf_{cls}_L{Ls[cls]}.log"), "w")
        node_L = 86400 if args.policy else Ls[cls]   # the live gateway enforces lifetimes
        subprocess.Popen(f"ip netns exec {NS_S} {UD} -e dtn://surf{i}.dtn/ -l {node_L} "
                         f"-a 127.0.0.1 -p {SURF_AAP0 + i} -c mtcp:{IP_S},{SURF_MTCP0 + i} -L {UD_LOG} "
                         f"-S /tmp/lb_surf{i}.aap2.sock",
                         shell=True, stdout=lm, stderr=lm)
    time.sleep(2.0)
    me = os.path.abspath(__file__)
    rxf, txf = os.path.join(args.out, "recv.jsonl"), os.path.join(args.out, "sent.jsonl")
    for f in (rxf, txf):
        open(f, "w").close()
    rx = subprocess.Popen(f"ip netns exec {NS_R} {PY} {me} rx --file {rxf}",
                          shell=True, preexec_fn=os.setsid)
    t0_dtn = int(time.time() - UNIX_TO_DTN) + args.lead
    contacts_dtn = [(t0_dtn + s, t0_dtn + e) for s, e in W]
    contacts_unix = [(s + UNIX_TO_DTN, e + UNIX_TO_DTN) for s, e in contacts_dtn]
    sched = dict(lifetimes=Ls, k=args.k, windows_rel=W, contacts_dtn=contacts_dtn,
                 contacts_unix=contacts_unix, t_start_unix=contacts_unix[0][0],
                 t_end_unix=contacts_unix[-1][1])
    sf = os.path.join(args.out, "schedule.json"); json.dump(sched, open(sf, "w"), indent=1)
    print(f"[run] {len(W)} windows, k={args.k}, span={W[-1][1] - W[0][0]} s, "
          f"lifetimes {Ls}; starts in {args.lead} s", flush=True)

    def toggler():
        for s, e in contacts_unix:
            while time.time() < s - NETEM_GUARD_S: time.sleep(0.01)
            netem(NS_S, VE_S, False); netem(NS_R, VE_R, False)
            while time.time() < e + NETEM_GUARD_S: time.sleep(0.01)
            netem(NS_S, VE_S, True); netem(NS_R, VE_R, True)
    threading.Thread(target=toggler, daemon=True).start()
    # the pcap stream arrives on our stdin; hand it to the in-namespace bridge
    if args.policy:
        cmd = (f"ip netns exec {NS_S} {PY} {me} gateway --schedule {sf} "
               f"--qos-map {args.qos_map} --file {txf} --policy {args.policy} "
               f"--admission {args.admission} --buffer-bytes {args.buffer_bytes} "
               f"--rate-bps {args.rate_bps}")
    else:
        cmd = (f"ip netns exec {NS_S} {PY} {me} bridge --schedule {sf} "
               f"--qos-map {args.qos_map} --file {txf}")
    subprocess.run(cmd, shell=True, stdin=sys.stdin)
    time.sleep(args.drain)
    os.killpg(os.getpgid(rx.pid), signal.SIGTERM)
    cleanup()
    analyze(args.out)


def analyze(out):
    sched = json.load(open(os.path.join(out, "schedule.json")))
    W, C, Ls = sched["windows_rel"], sched["contacts_unix"], sched["lifetimes"]
    sent = [json.loads(l) for l in open(os.path.join(out, "sent.jsonl"))]
    got = {int(json.loads(l)["h"].split(":")[0]) for l in open(os.path.join(out, "recv.jsonl"))}
    Gmax = max(W[j + 1][0] - W[j][1] for j in range(len(W) - 1))
    rows = []
    for cls in CLASSES:
        mine = [s for s in sent if s["cls"] == cls]
        if not mine:
            continue
        L = Ls[cls]
        d = sum(s["seq"] in got for s in mine)
        rows.append(dict(cls=cls, fiveqi=mine[0]["5qi"], qfi=sorted({s["qfi"] for s in mine}),
                         L=L, rho=L / Gmax, sent=len(mine), delivered=d,
                         measured=d / len(mine),
                         per_bundle=sum(predict_one(s["t"], C, L) for s in mine) / len(mine),
                         closed_form=closed_form(W, L)))
    gwf = os.path.join(out, "gw.jsonl")
    if os.path.exists(gwf):                    # live gateway: fate of every bundle
        fate = {}
        for l in open(gwf):
            e = json.loads(l)
            fate.setdefault(e["cls"], {}).setdefault(e["ev"], 0)
            fate[e["cls"]][e["ev"]] += 1
        for r in rows:
            r["gateway"] = fate.get(r["cls"], {})
            print(f"  {r['cls']:10s} gateway fates {r['gateway']}")
    json.dump(rows, open(os.path.join(out, "results.json"), "w"), indent=1)
    for r in rows:
        print(f"{r['cls']:10s} 5QI {r['fiveqi']:>2} QFI {r['qfi']} L {r['L']:>3}s "
              f"rho {r['rho']:.2f} sent {r['sent']:>6} deliv {r['delivered']:>6} "
              f"measured {r['measured']:.3f} per-bundle {r['per_bundle']:.3f} "
              f"closed {r['closed_form']:.3f}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--plan", required=True); r.add_argument("--windows", type=int, default=8)
    r.add_argument("--k", type=float, default=720); r.add_argument("--qos-map", required=True)
    r.add_argument("--lead", type=int, default=15); r.add_argument("--drain", type=float, default=5.0)
    r.add_argument("--out", required=True)
    r.add_argument("--policy", choices=["fifo", "strict", "wfq"], default=None,
                   help="run the live gateway scheduler behind the bridge")
    r.add_argument("--admission", choices=["droptail", "priority"], default="droptail")
    r.add_argument("--buffer-bytes", type=float, default=2e6)
    r.add_argument("--rate-bps", type=float, default=5e5)
    g = sub.add_parser("gateway"); g.add_argument("--schedule", required=True)
    g.add_argument("--qos-map", required=True); g.add_argument("--file", required=True)
    g.add_argument("--policy", required=True); g.add_argument("--admission", required=True)
    g.add_argument("--buffer-bytes", type=float, required=True)
    g.add_argument("--rate-bps", type=float, required=True)
    x = sub.add_parser("rx"); x.add_argument("--file", required=True)
    b = sub.add_parser("bridge"); b.add_argument("--schedule", required=True)
    b.add_argument("--qos-map", required=True); b.add_argument("--file", required=True)
    a = sub.add_parser("analyze"); a.add_argument("--out", required=True)
    args = ap.parse_args()
    {"run": run, "rx": worker_rx, "bridge": worker_bridge, "gateway": worker_gateway,
     "analyze": lambda a: analyze(a.out)}[args.cmd](args)


if __name__ == "__main__":
    main()
