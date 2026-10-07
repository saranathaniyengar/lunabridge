#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
"""
channel/grc_lunar.py

Lunar channel IN THE LOOP for the live srsRAN ZMQ testbed. Wraps the deployed
Microsoft GNU Radio broker (GRC_multi_ue_headless.py: multi_ue_scenario) and,
instead of a static per-UE pathloss, DRIVES the per-UE DL+UL pathloss from a
real lunar-terrain trace produced by lunaremu (channel/lunaremu_to_pathloss.py).

How: multi_ue_scenario builds per-UE multiply_const_cc blocks
(blocks_multiply_const_dl_pathloss[ue], _ul_). Those support set_k() at runtime,
so after tb.start() a player thread steps the trace and sets
k = 10**(-pl_db/20) per UE per snapshot. An outage sample zeros the link.

This is the light "lunar channel without Colosseum" path: real srsRAN traffic
over a real south-pole terrain channel, no X410/Colosseum reservation needed.

Deploy: drop this file next to GRC_multi_ue_headless.py in the broker image (or
mount it), mount the trace JSON, and launch this instead of the stock script:

  python3 grc_lunar.py --gnb-addr <...> --ue-addrs "<a> <b>" \
      --ue-tx-ports "2101 2201" --ue-rx-ports "2100 2200" \
      --samp-rate 11520000 --slowdown 2 \
      --pathloss-trace connecting_ridge.pathloss.json --trace-speedup 1.0

Every other arg matches the stock broker. Without --pathloss-trace it behaves
exactly like the stock broker (static --ue-pathloss).
"""
import argparse, json, signal, sys, threading, time

from gnuradio import analog, blocks           # noise floor blocks
from GRC_multi_ue_headless import multi_ue_scenario  # the deployed broker


def _space_list(s, cast):
    return [cast(x) for x in str(s).replace(",", " ").split()]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--gnb-addr', default='127.0.0.1')
    p.add_argument('--gnb-tx-port', type=int, default=2000)
    p.add_argument('--gnb-rx-port', type=int, default=2001)
    p.add_argument('--ue-addrs', default='127.0.0.1')
    p.add_argument('--ue-tx-ports', default='2101')
    p.add_argument('--ue-rx-ports', default='2100')
    p.add_argument('--ue-pathloss', default='0', help='initial/fallback dB per UE')
    p.add_argument('--slowdown', type=float, default=4.0)
    p.add_argument('--samp-rate', type=float, default=11520000)
    p.add_argument('--zmq-timeout', type=int, default=100)
    p.add_argument('--zmq-hwm', type=int, default=-1)
    p.add_argument('--pathloss-trace', default=None,
                   help='JSON [{"t":s,"pl_db":[ue1,ue2,...],"outage":[0,1,...]}]')
    p.add_argument('--trace-speedup', type=float, default=1.0,
                   help='replay factor: 1.0 = real lunaremu time (1 snap/s)')
    p.add_argument('--loop-trace', action='store_true', help='repeat the trace')
    p.add_argument('--noise-amp', type=float, default=0.0,
                   help='per-UE DL Gaussian noise amplitude. >0 inserts a '
                        'calibrated noise floor so path loss produces real SNR '
                        '(SNR_at_0dB ~ -20*log10(noise_amp/sig_rms)). 0 = off '
                        '(pure attenuation, legacy). Calibrate so the UE just '
                        'attaches at pl=0 and degrades as the rover ramps.')
    return p.parse_args()


OUTAGE_PL_DB = 200.0  # k = 10**(-200/20) ~ 1e-10 : link effectively dark


def measure_signal(tb, stop_evt):
    """Tap the gNB DL stream and log its RMS so the noise floor can be
    calibrated to a target SNR (noise_amp = rms * 10^(-SNR_dB/20))."""
    rms = blocks.rms_cf(0.0001)
    probe = blocks.probe_signal_f()
    tb.connect((tb.blocks_throttle, 0), (rms, 0))
    tb.connect((rms, 0), (probe, 0))
    tb._probe_blocks = [rms, probe]

    def loop():
        while not stop_evt.is_set():
            stop_evt.wait(5)
            try:
                print(f"[LUNAR] gNB DL signal RMS = {probe.level():.6f}", flush=True)
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True).start()


def add_noise_floor(tb, noise_amp, num_ues):
    """Insert a Gaussian noise floor on each UE downlink, BEFORE the flowgraph
    starts, so per-UE path-loss attenuation produces a real SNR rather than a
    scale-invariant amplitude change the UE's AGC would undo. Rewires
    pathloss_block -> ue_dl_sink into pathloss_block -> add_cc(+noise) ->
    ue_dl_sink. References are kept on tb so the blocks are not garbage
    collected."""
    tb._noise_blocks = []
    for ue in range(num_ues):
        pl_blk = tb.blocks_multiply_const_dl_pathloss[ue]
        sink = tb.ue_dl_sinks[ue]
        noise = analog.noise_source_c(analog.GR_GAUSSIAN, noise_amp, ue + 1)
        adder = blocks.add_cc()
        tb.disconnect((pl_blk, 0), (sink, 0))
        tb.connect((pl_blk, 0), (adder, 0))
        tb.connect((noise, 0), (adder, 1))
        tb.connect((adder, 0), (sink, 0))
        tb._noise_blocks += [noise, adder]
        print(f"[LUNAR] noise floor inserted on UE{ue+1} DL (amp={noise_amp})",
              flush=True)


def apply_pathloss(tb, pls_db):
    """Set per-UE DL+UL gains from a list of path losses (dB)."""
    n = min(len(pls_db), len(tb.blocks_multiply_const_dl_pathloss))
    for ue in range(n):
        k = 10.0 ** (-float(pls_db[ue]) / 20.0)
        tb.blocks_multiply_const_dl_pathloss[ue].set_k(k)
        tb.blocks_multiply_const_ul_pathloss[ue].set_k(k)


def player(tb, trace, speedup, loop, stop_evt):
    """Step the trace in wall-clock, updating per-UE pathloss via set_k."""
    while not stop_evt.is_set():
        t0 = time.monotonic()
        base = trace[0]["t"]
        for snap in trace:
            if stop_evt.is_set():
                return
            target = (snap["t"] - base) / max(speedup, 1e-9)
            dt = target - (time.monotonic() - t0)
            if dt > 0:
                stop_evt.wait(dt)
            pls = list(snap["pl_db"])
            for i, o in enumerate(snap.get("outage", [])):
                if o:
                    pls[i] = OUTAGE_PL_DB
            apply_pathloss(tb, pls)
            print(f"[LUNAR] t={snap['t']:.0f}s pl_db={pls}", flush=True)
        if not loop:
            print("[LUNAR] trace done; holding last channel.", flush=True)
            return


def main():
    a = parse_args()
    ue_addrs = _space_list(a.ue_addrs, str)
    tx = _space_list(a.ue_tx_ports, int)
    rx = _space_list(a.ue_rx_ports, int)
    init_pl = _space_list(a.ue_pathloss, float)
    if len(init_pl) < len(ue_addrs):
        init_pl = (init_pl + [0.0] * len(ue_addrs))[:len(ue_addrs)]

    trace = None
    if a.pathloss_trace:
        trace = json.load(open(a.pathloss_trace))
        init_pl = [float(x) for x in trace[0]["pl_db"]][:len(ue_addrs)]

    tb = multi_ue_scenario(
        gnb_addr=a.gnb_addr, gnb_tx_port=a.gnb_tx_port, gnb_rx_port=a.gnb_rx_port,
        ue_addrs=tuple(ue_addrs), ue_tx_ports=tuple(tx), ue_rx_ports=tuple(rx),
        ue_pathloss=tuple(init_pl), slow_down_ratio=a.slowdown,
        samp_rate=a.samp_rate, zmq_timeout=a.zmq_timeout, zmq_hwm=a.zmq_hwm)

    stop_evt = threading.Event()
    measure_signal(tb, stop_evt)          # log gNB DL RMS for noise calibration
    if a.noise_amp > 0:
        add_noise_floor(tb, a.noise_amp, len(ue_addrs))


    def sig(*_):
        stop_evt.set(); tb.stop(); tb.wait(); sys.exit(0)
    signal.signal(signal.SIGINT, sig); signal.signal(signal.SIGTERM, sig)

    tb.start()
    if trace:
        th = threading.Thread(target=player,
                              args=(tb, trace, a.trace_speedup, a.loop_trace, stop_evt),
                              daemon=True)
        th.start()
        print(f"[LUNAR] driving pathloss from {a.pathloss_trace} "
              f"({len(trace)} snapshots, speedup={a.trace_speedup})", flush=True)
    else:
        print("[LUNAR] no trace: static pathloss (stock behavior)", flush=True)
    tb.wait()


if __name__ == "__main__":
    main()
