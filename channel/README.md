# channel — lunar RF channel in the loop (no Colosseum needed)

Drives the **live srsRAN ZMQ testbed**'s per-UE pathloss from a **real
south-pole terrain trace** (lunaremu), so real srsRAN traffic crosses a real
lunar surface channel — the light-weight alternative to a Colosseum/X410
reservation.

## Pieces
- `lunaremu_to_pathloss.py` — `expected_links.csv` (lunaremu) → per-UE pathloss
  trace JSON. Verified on Connecting Ridge: UE1_LOS ≈94 dB constant, UE3_rover
  87→174 dB as it drives into terrain shadow.
- `grc_lunar.py` — wraps the deployed broker's `multi_ue_scenario` and updates
  `blocks_multiply_const_{dl,ul}_pathloss[ue].set_k(10**(-pl/20))` per snapshot
  from the trace. Drop-in: same CLI as the stock broker plus `--pathloss-trace`.

## Deploy (on the broker pod, namespace `ran`)
```bash
# 1. build the trace from a lunaremu scenario
python3 channel/lunaremu_to_pathloss.py \
    lunar-colosseum-oai/scenarios/out/connecting_ridge_4node/expected_links.csv \
    -o ridge.pathloss.json --ue-order UE1_LOS UE3_rover

# 2. mount grc_lunar.py + ridge.pathloss.json into the broker image/pod and
#    launch it INSTEAD of GRC_multi_ue_headless.py (same args + the trace):
python3 grc_lunar.py --gnb-addr srs-gnb-du1-zmq.ran.svc.cluster.local \
    --ue-addrs "srs-ue1-du1-zmq.ran.svc.cluster.local srs-ue2-du1-zmq.ran.svc.cluster.local" \
    --ue-tx-ports "2101 2201" --ue-rx-ports "2100 2200" \
    --samp-rate 11520000 --slowdown 2 \
    --pathloss-trace ridge.pathloss.json --trace-speedup 1.0
```
Without `--pathloss-trace` it is byte-for-byte the stock broker (static pathloss).

## Known fidelity caveat (must address for real BLER)
The deployed GRC flowgraph applies pathloss as **attenuation only**
(`multiply_const_cc`) with **no noise floor** in the graph. Pure amplitude
scaling does not by itself produce realistic SNR/BLER in srsRAN ZMQ — the
Microsoft broker uses pathloss as a *relative* knob between UEs. To make
**absolute** lunar path loss (87–180 dB) translate into real MCS adaptation and
radio-link failure, inject a calibrated noise floor: add a
`analog.noise_source_c` + `blocks.add_cc` on each UE DL path so SNR = Psig(k) −
Pnoise. This mirrors what Colosseum/LCHEM do with their own noise floor.
`grc_lunar.py` is structured to add this next (one noise+add block per UE);
flagged here rather than silently shipping attenuation-only.

## Where this fits
Access-channel realism for the surface-5G island. The relay/Earth delay +
blackout + DTN remain the netem + µD3TN tail (`link_controller/`), deployed at
the UPF N6. Together: real traffic → real lunar access channel → N6 → DTN →
Earth.
