#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
"""
channel/grc_lunar_standalone.py

STANDALONE lunar-channel broker. This copies the deployed Microsoft multi-UE
flowgraph VERBATIM (so the DL path moves samples exactly as the working stock
broker does -- importing and reusing the class was what broke the DL) and makes
ONE change: each UE downlink goes through a proper GNU Radio
channels.channel_model instead of a bare multiply_const. channel_model applies
the path-loss tap AND a real AWGN noise floor (noise_voltage), so attenuation
yields a real SNR the UE's AGC cannot undo -- graceful MCS degradation as the
rover's path loss rises, and outage in deep shadow.

A trace player drives each UE's tap over time from a lunaremu terrain path-loss
trace (channel/lunaremu_to_pathloss.py). An optional RMS probe on the gNB DL
lets you calibrate noise_voltage (noise_voltage = sig_rms * 10^(-SNR_dB/20)).

Deploy exactly like the stock broker but run this instead of GRC_run.sh, adding
--pathloss-trace and --noise-voltage.
"""
import sys, signal, argparse, json, threading, time
from gnuradio import blocks, gr, zeromq, channels


class multi_ue_scenario(gr.top_block):
    def __init__(self, gnb_addr='127.0.0.1', gnb_tx_port=2000, gnb_rx_port=2001,
                 ue_addrs=('127.0.0.1',), ue_tx_ports=(2101,), ue_rx_ports=(2100,),
                 ue_pathloss=(0.0,), slow_down_ratio=4.0, samp_rate=11520000,
                 zmq_timeout=100, zmq_hwm=-1, noise_voltage=0.0):
        print("[LUNAR] Initializing standalone lunar-channel scenario...", flush=True)
        num_ues = len(ue_addrs)
        gr.top_block.__init__(self, "srsRAN_multi_UE_lunar", catch_exceptions=True)
        self.samp_rate = float(samp_rate)
        self.slow_down_ratio = float(slow_down_ratio)
        self.noise_voltage = float(noise_voltage)

        # ZMQ endpoints -- identical to the stock broker.
        self.gnb_dl_source = zeromq.req_source(gr.sizeof_gr_complex, 1, f"tcp://{gnb_addr}:{gnb_tx_port}", zmq_timeout, False, zmq_hwm)
        self.gnb_ul_sink = zeromq.rep_sink(gr.sizeof_gr_complex, 1, f"tcp://0.0.0.0:{gnb_rx_port}", zmq_timeout, False, zmq_hwm)
        self.ue_ul_sources = [zeromq.req_source(gr.sizeof_gr_complex, 1, f"tcp://{a}:{p}", zmq_timeout, False, zmq_hwm)
                              for a, p in zip(ue_addrs, ue_tx_ports)]
        self.ue_dl_sinks = [zeromq.rep_sink(gr.sizeof_gr_complex, 1, f"tcp://0.0.0.0:{p}", zmq_timeout, False, zmq_hwm)
                            for p in ue_rx_ports]

        self.blocks_throttle = blocks.throttle(gr.sizeof_gr_complex*1,
                                               self.samp_rate/self.slow_down_ratio, True)
        self.gains = [10**(-pl/20.0) for pl in ue_pathloss]

        # --- THE CHANGE: UE DL via channel_model (path-loss tap + noise floor) ---
        # frequency_offset=0, epsilon=1.0 (no timing drift), one tap = path-loss
        # gain, per-UE noise_seed. noise_voltage is the AWGN std (the floor).
        self.channel_model_dl = [
            channels.channel_model(noise_voltage=self.noise_voltage,
                                   frequency_offset=0.0, epsilon=1.0,
                                   taps=(complex(k), ), noise_seed=ue + 1,
                                   block_tags=False)
            for ue, k in enumerate(self.gains)]
        # UL unchanged (bare attenuation) -- UL fidelity is not the UE-sync concern.
        self.blocks_multiply_const_ul_pathloss = [blocks.multiply_const_cc(k) for k in self.gains]
        self.blocks_add_xx_0 = blocks.add_vcc(1)

        # --- Connections (same topology as stock; DL block swapped) ---
        self.connect((self.gnb_dl_source, 0), (self.blocks_throttle, 0))
        for ue in range(num_ues):
            self.connect((self.blocks_throttle, 0), (self.channel_model_dl[ue], 0))
            self.connect((self.channel_model_dl[ue], 0), (self.ue_dl_sinks[ue], 0))
        for ue in range(num_ues):
            self.connect((self.ue_ul_sources[ue], 0), (self.blocks_multiply_const_ul_pathloss[ue], 0))
        for port, m in enumerate(self.blocks_multiply_const_ul_pathloss):
            self.connect((m, 0), (self.blocks_add_xx_0, port))
        self.connect((self.blocks_add_xx_0, 0), (self.gnb_ul_sink, 0))
        print(f"[LUNAR] initialized: {num_ues} UE DL channel_models, "
              f"noise_voltage={self.noise_voltage}", flush=True)


def set_ue_pathloss(tb, ue, pl_db, outage):
    k = 1e-6 if outage else 10.0 ** (-float(pl_db) / 20.0)
    tb.channel_model_dl[ue].set_taps((complex(k), ))


def attach_probe(tb, stop_evt):
    """Log gNB DL RMS for noise calibration (noise_voltage = rms*10^(-SNR/20))."""
    rms = blocks.rms_cf(0.0001); probe = blocks.probe_signal_f()
    tb.connect((tb.blocks_throttle, 0), (rms, 0)); tb.connect((rms, 0), (probe, 0))
    tb._probe = (rms, probe)
    import math
    def loop():
        while not stop_evt.is_set():
            stop_evt.wait(10)
            try:
                rms = float(probe.level())
                if tb.noise_voltage > 0 and rms > 0:
                    snr = 20 * math.log10(rms / tb.noise_voltage)
                    print(f"[LUNAR] gNB DL RMS={rms:.5f} (SNR@0dB~{snr:.0f}dB)", flush=True)
                else:
                    print(f"[LUNAR] gNB DL RMS={rms:.5f} "
                          f"(noise_voltage={tb.noise_voltage})", flush=True)
            except Exception as e:
                print(f"[LUNAR] probe err: {e}", flush=True)
    threading.Thread(target=loop, daemon=True).start()


def player(tb, trace, speedup, loop, stop_evt, start_delay=0.0):
    # hold the initial (LOS) channel so the UE can attach before the traverse
    if start_delay > 0:
        print(f"[LUNAR] holding initial channel {start_delay:.0f}s for UE attach",
              flush=True)
        stop_evt.wait(start_delay)
    while not stop_evt.is_set():
        t0 = time.monotonic(); base = trace[0]["t"]
        for snap in trace:
            if stop_evt.is_set(): return
            dt = (snap["t"] - base) / max(speedup, 1e-9) - (time.monotonic() - t0)
            if dt > 0: stop_evt.wait(dt)
            og = snap.get("outage", [0] * len(snap["pl_db"]))
            for ue, pl in enumerate(snap["pl_db"]):
                if ue < len(tb.channel_model_dl):
                    set_ue_pathloss(tb, ue, pl, og[ue] if ue < len(og) else 0)
            print(f"[LUNAR] t={snap['t']:.0f}s pl_db={snap['pl_db']} outage={og}", flush=True)
        if not loop:
            print("[LUNAR] trace done; holding last channel.", flush=True); return


def parse_args():
    sl = lambda s, c: [c(x) for x in str(s).replace(',', ' ').split()]
    p = argparse.ArgumentParser()
    p.add_argument('--gnb-addr', default='127.0.0.1'); p.add_argument('--gnb-tx-port', type=int, default=2000)
    p.add_argument('--gnb-rx-port', type=int, default=2001)
    p.add_argument('--ue-addrs', default='127.0.0.1'); p.add_argument('--ue-tx-ports', default='2101')
    p.add_argument('--ue-rx-ports', default='2100'); p.add_argument('--ue-pathloss', default='0')
    p.add_argument('--slowdown', type=float, default=4.0); p.add_argument('--samp-rate', type=float, default=11520000)
    p.add_argument('--zmq-timeout', type=int, default=100); p.add_argument('--zmq-hwm', type=int, default=-1)
    p.add_argument('--noise-voltage', type=float, default=0.0, help='AWGN std (noise floor)')
    p.add_argument('--pathloss-trace', default=None); p.add_argument('--trace-speedup', type=float, default=1.0)
    p.add_argument('--loop-trace', action='store_true'); p.add_argument('--probe', action='store_true')
    p.add_argument('--trace-start-delay', type=float, default=0.0,
                   help='hold initial channel this many seconds (UE attach)')
    a = p.parse_args()
    for f in ('ue_addrs', 'ue_tx_ports', 'ue_rx_ports', 'ue_pathloss'):
        setattr(a, f, sl(getattr(a, f), str if f == 'ue_addrs' else (int if 'port' in f else float)))
    return a


def main():
    a = parse_args()
    init_pl = a.ue_pathloss
    trace = None
    if a.pathloss_trace:
        trace = json.load(open(a.pathloss_trace))
        init_pl = [float(x) for x in trace[0]["pl_db"]][:len(a.ue_addrs)]
    if len(init_pl) < len(a.ue_addrs):
        init_pl = (init_pl + [0.0]*len(a.ue_addrs))[:len(a.ue_addrs)]

    tb = multi_ue_scenario(gnb_addr=a.gnb_addr, gnb_tx_port=a.gnb_tx_port, gnb_rx_port=a.gnb_rx_port,
                           ue_addrs=tuple(a.ue_addrs), ue_tx_ports=tuple(a.ue_tx_ports),
                           ue_rx_ports=tuple(a.ue_rx_ports), ue_pathloss=tuple(init_pl),
                           slow_down_ratio=a.slowdown, samp_rate=a.samp_rate,
                           zmq_timeout=a.zmq_timeout, zmq_hwm=a.zmq_hwm, noise_voltage=a.noise_voltage)
    stop_evt = threading.Event()
    def sig(*_):
        stop_evt.set()
        try: tb.stop(); tb.wait()
        except Exception: pass
        sys.exit(0)
    signal.signal(signal.SIGINT, sig); signal.signal(signal.SIGTERM, sig)
    if a.probe: attach_probe(tb, stop_evt)
    print("[LUNAR] Starting flowgraph...", flush=True)
    tb.start()
    if trace:
        threading.Thread(target=player, args=(tb, trace, a.trace_speedup, a.loop_trace,
                         stop_evt, a.trace_start_delay), daemon=True).start()
        print(f"[LUNAR] driving pathloss from {a.pathloss_trace} ({len(trace)} snaps)", flush=True)
    tb.wait()


if __name__ == '__main__':
    main()
