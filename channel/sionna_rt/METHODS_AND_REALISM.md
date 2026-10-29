# Lunar access channel in the live loop — methods, realism, and reviewer defense

This document explains, in full, how the LunaBridge lunar **access channel** is
produced and driven into the live srsRAN + DTN testbed, **why we consider it
realistic**, how it **connects to the LunaCov coverage paper**, and the
**assumptions and limitations** a reviewer will probe. It is written to be
self-contained: a reviewer should be able to reconstruct and challenge every
number from here.

---

## 0. One-paragraph summary

The access channel is computed by ray tracing over the **real** PGDA LOLA
5 m/px south-pole terrain (Connecting Ridge, Site01), with a lunar-regolith
dielectric taken from the measured density–permittivity law and the Siegler et
al. (2020) loss tangent. This is the **same ray-traced model (Sionna RT) and the
same physical inputs** as *LunaTwin* in the LunaCov coverage paper — so our
channel inherits LunaCov's validation (agreement with the analytic planner to
within 0.5 dB, diffraction verified against ITU-R P.526, and cross-checked
against 2095 terrestrial UHF measurements). The **new** contribution here is not
the channel model: it is that we take this terrain-grounded channel and drive a
**real 5G stack** (srsRAN gNB + Open5GS core + srsUE) and a **real DTN** at the
N6 reference point, closing the full *channel × traffic × DTN* loop in hardware-
in-the-loop fashion — which LunaCov lists as future work (its hardware target is
LCHEM/USRP and its 5G link level is MATLAB software).

---

## 1. Relationship to LunaCov / LunaTwin (read this first)

LunaCov (companion paper, *"Lunar Surface 5G Networks: Terrain- and Regolith-
Aware Propagation and Coverage"*) already contains:

- **LunaCov** — an analytic planner: spherical LOS + multi-edge **Deygout**
  diffraction + **two-ray** ground reflection + Fresnel regolith, on LOLA terrain,
  each model unit-tested against literature (e.g. ITU-R P.526 knife-edge loss) and
  validated against terrestrial UHF measurements.
- **LunaTwin** — "an independent ray-traced model of the same terrain, built with
  **Sionna RT**": DEM → triangulated spherical-cap mesh, single regolith material
  (`ε'=1.919^ρ`, Siegler `tanδ`), isotropic V-pol single elements, solver with
  LOS + specular + wedge/edge diffraction, refraction disabled, ≤3 interactions.
- **Hardware Channel Export** — taps → MATLAB `nrTDLChannel` (software 5G) and
  → **LCHEM** (RFNoC HIL emulator on a USRP X410). The USRP/Colosseum replay is
  marked **future work**.

**Our access channel (`lunar_rt_lola.py`) is deliberately LunaTwin**: same tool
(Sionna RT 2.2 / Mitsuba 3.9), same DEM, same regolith model, same solver
configuration (isotropic V-pol, LOS + specular + edge/wedge diffraction,
refraction off, 3 interactions), same spherical-cap curvature about the 10 km
tile centre. We did this on purpose so there is **no methodological daylight**
between the two papers: the channel that drives our live loop is the one LunaCov
already validated.

**What LunaBridge adds:** the *live 5G-stack-in-the-loop with DTN*. LunaCov
exports to LCHEM (RF HIL, future work) and to MATLAB (software link level);
LunaBridge instead feeds the per-position channel into the srsRAN ZMQ broker so a
**real gNB/UE/core** carry **real class-marked traffic** that is intercepted at
**N6** and handed to a **real DTN** — the end-to-end coupling LunaCov cannot show.

---

## 2. Physical inputs (shared, cited)

| Quantity | Value / source |
|---|---|
| DEM | PGDA LOLA **5 m/px**, Barker et al. (2021), Site01 **Connecting Ridge**; polar-stereographic, sphere R=1 737 400 m; 10 km tile |
| Carrier | **2.5 GHz** S-band (SFCG/LCRNS surface allocation) |
| Regolith ε′ | `ε' = 1.919^ρ` (density–permittivity law); ρ=1.5 g/cm³ → **ε′=2.658** |
| Regolith tanδ | Siegler et al. (2020): `tanδ = 10^(0.312ρ + f_GHz^0.069 − 3.79)` → **0.0055** at 2.5 GHz |
| Conductivity | `σ = 2π f ε₀ ε′ tanδ` = **2.05×10⁻³ S/m** |
| Terminals | isotropic, vertically polarised single elements (omni S-band, matches the §II link budget) |
| gNB | tile-maximum point, **30 m** mast (lunaremu `tile_max` placement) |
| Rover (UE) | radial "cliff" traverse, **2 m** height, ~300 → ~900 m ground range, 1 m/s |

These are **identical** to LunaCov/LunaTwin, which is the point.

---

## 3. Pipeline (reproducible)

```
PGDA LOLA Site01 DEM  ──(lunaremu build_scenario: gNB@tile-max + radial cliff rover)
   │  export_lola_scene.py  →  lola_scene.npz  (clipped DEM + node pixel positions)
   ▼
lunar_rt_lola.py
   • spherical-cap curvature about the tile centre  (== LunaTwin)
   • crop a patch around the gNB+rover corridor; triangulate (2 tris/cell)
   • regolith RadioMaterial (ε′, σ above)
   • Sionna RT PathSolver: LOS + specular + wedge/edge diffraction,
     refraction OFF, max_depth=3, isotropic V-pol
   • per rover position: path gain → path loss(dB);  RMS delay spread from the CIR
   ├─►  figs/lunar_rt_lola.pdf      (path loss vs ground range: LOS → cliff → shadow)
   └─►  connecting_ridge.pathloss.json   (per-position pl_dB + outage flag)
           │  (relative-to-LOS + outage flag; see §5)
           ▼
grc_lunar_standalone.py  (srsRAN GRC ZMQ broker)
   • each UE downlink → channels.channel_model(tap = 10^(−pl_rel/20), AWGN floor)
   • trace player steps the tap per position; outage → tap 1e-6
           ▼
   srsRAN gNB ─ Open5GS core ─ srsUE   (live, class-marked traffic)
           ▼
   N6 (ogstun) interception → BPv7 bundles → DTN store-carry-forward
```

Versions: Sionna RT 2.2.0, Mitsuba 3.9.1 (Metal/CUDA/LLVM), srsRAN `srsran25.10`
images (Microsoft jrtc-apps), Open5GS, GNU Radio broker. Exact commands in
`README.md`.

---

## 4. Why the channel is realistic

1. **Real terrain, not a model.** Propagation is traced over the actual LOLA
   5 m/px Connecting Ridge DEM — the same product used across the Artemis
   site-selection literature and in LunaCov. The coverage "cliffs" fall at real
   crater rims, not a parameterised fade.
2. **Measured regolith EM.** ε′ from the laboratory density–permittivity law and
   tanδ from Siegler et al. (2020) remote-sensing fits — not invented constants.
   Identical to LunaCov/LunaTwin.
3. **First-principles propagation.** Sionna RT solves Maxwellian ray optics
   (Fermat paths, Fresnel reflection off regolith, UTD wedge/edge diffraction) on
   the 3-D surface, capturing (i) the two-ray interference lobes in LOS, (ii)
   diffraction at crater rims, and (iii) hard geometric shadowing — the three
   features that define a lunar surface link.
4. **Cross-validated (inherited).** LunaCov reports LunaTwin (this exact RT model)
   agreeing with the analytic two-ray/Deygout planner **within 0.5 dB** in LOS,
   with the analytic diffraction verified against ITU-R P.526 and the whole engine
   validated against **2095** terrestrial UHF measurements in lunar-like terrain.
   Our channel is that model, so it carries that validation.
5. **Frequency-flat is proven, not assumed.** We compute the **RMS delay spread
   from the RT channel impulse response** itself: **median 0.1 ns, max 0.7 ns**
   across the LOS traverse. The NR cyclic prefix is **2.34 µs** (30 kHz SCS) to
   4.69 µs (15 kHz) — ~3–4 orders of magnitude larger. The access channel is
   therefore flat over the NR bandwidth, and a **single complex tap per position**
   (what we drive into the loop) is an exact representation, not a convenience.
   (LunaCov's LCHEM export keeps up to 16 taps because it is general; for this
   site/geometry the measured spread collapses that to one.)

---

## 5. The live loop and its calibration (and why it is honest)

The srsRAN ZMQ path exchanges **baseband IQ** between gNB and UE; it has no
antenna, no noise temperature, **no absolute link budget**. You therefore cannot
inject an absolute 90 dB path loss (it would simply zero the samples). This is the
*same* problem LunaCov solves for LCHEM, and we solve it the *same* way:

- **Separate shape from level.** LunaCov/LCHEM scale coefficients **relative to the
  free-space direct ray** and apply absolute attenuation in the radio's digital
  attenuator + gains. We apply the trace **relative to the LOS minimum**
  (`pl_rel = pl − pl_min`) and realise it as a `channel_model` tap, with a
  **calibrated AWGN floor** so the relative attenuation produces a real SNR the
  UE's AGC cannot undo.
- **Operating point.** `--probe` measures the gNB DL IQ RMS (0.0555);
  `noise_voltage = RMS·10^(−SNR₀/20)` sets the LOS SNR. We use **SNR₀ = 25 dB**
  (a good-link operating point), so SNR(t) = 25 − pl_rel(t) dB along the traverse.
- **Outage.** RT shadow positions carry an explicit outage flag → tap 1e-6
  (link dark). The *absolute* surface budget (what makes 90–160 dB a real outage)
  lives in the LunaBridge §II link-budget table and in LunaCov; the loop reproduces
  the **relative** dynamics and the **outage boundary**, which is what governs
  throughput and DTN behaviour.
- **Attach handling.** `--trace-start-delay` holds the LOS channel while the UE
  attaches, then plays the traverse.

**Result (2026-10-07):** steady **~4.5 Mbps** DL across the LOS traverse, a
**vertical collapse to 0** at the crater-rim cliff, sustained outage through the
shadow, and recovery when the looped traverse returns to LOS
(`figs/closed_loop_final.png`). This is "cliff, not a fade" at the **live-system
throughput** level, with the bundles backing up at N6 — the channel × traffic ×
DTN coupling.

---

## 6. Assumptions and limitations (state these before a reviewer does)

1. **Deep shadow = hard RT outage.** With refraction disabled (a zero-thickness
   surface would unphysically leak rays through ridges — the same reason LunaTwin
   disables it), RT finds **no** path into deep shadow, so it reports outage where
   the analytic Deygout model gives a finite ~170 dB. The truth is bounded between
   a knife-edge diffracted floor and full obstruction; we report RT outage and cite
   the analytic floor. For the loop this is immaterial (both are "no service").
2. **DEM resolution (5 m).** Sub-5 m rim sharpness is unresolved; decimation
   (LunaTwin stride 3 = 15 m; we use full 5 m on a ~1 km patch) smooths edges and
   changes rim diffraction. Diffraction into shadow is thus a **lower bound**.
   LunaCov showed coverage was unchanged refining 15 m → 5 m on a 6 km tile.
3. **Homogeneous regolith half-space.** No subsurface layering, buried rocks, or
   sub-grid surface roughness beyond the DEM; a single radio material. Standard for
   this class of study and consistent with LunaCov.
4. **Operating-point SNR, not an absolute budget.** The 25 dB LOS SNR is a chosen
   baseband reference for the HIL loop; absolute served/not-served is set by the
   §II link budget and by LunaCov's coverage maps, not by the loop.
5. **Testbed artefacts.** `slowdown=2` scales wall-clock; gNB metrics are sparse
   (~1/6 s); a saturating flood destabilises the slowed link, so a gentle (~1 Mbps)
   sustained load is used for the clean throughput trace; the clean single-UE loop
   avoids the ZMQ UL-adder lockstep coupling that couples a second UE's outage.
6. **Single polarisation, isotropic elements.** Matches the omni S-band budget;
   directional/MIMO gains are out of scope (and handed to the space-segment study).

---

## 7. Anticipated reviewer questions

- **"Is this a real channel or a toy?"** Real: LOLA terrain + measured regolith +
  ray tracing; it is LunaCov's validated LunaTwin model (§1, §4).
- **"Why single-tap?"** Measured RMS delay spread 0.1 ns ≪ 2.34 µs CP (§4.5).
- **"Why does throughput sit at one level then drop, instead of fading?"** Because
  the lunar surface boundary is a geometric cliff, not a multipath fade (§4, §5);
  the RT CIR shows LOS two-ray lobes then abrupt shadow.
- **"Your absolute path loss can't be 7 dB — a 900 m S-band link is ~100 dB."**
  Correct; `figs/lunar_rt_lola.png` reports the **absolute** RT path loss
  (89–95 dB LOS, matching FSPL+two-ray). The **loop** uses path loss **relative to
  LOS** plus a calibrated noise floor because the ZMQ baseband has no link budget
  (§5) — exactly LunaCov's LCHEM shape/level split.
- **"How is this different from LunaTwin/LunaCov?"** Same channel; new is the live
  srsRAN + DTN loop vs LunaCov's MATLAB link level and future-work LCHEM (§1).
- **"Refraction off overstates shadow depth."** Acknowledged and bounded (§6.1).
- **"Reproducible?"** Yes — versions, data provenance, and commands in README; the
  trace JSON is the single artefact linking RT to the live loop.

---

## 8. Connection to LunaCov (companion)

- **Shared, validated inputs:** same DEM (Barker 2021 LOLA 5 m/px, Connecting
  Ridge Site01), same regolith (density law + Siegler 2020), same carrier, same
  isotropic V-pol terminals, same spherical-cap geometry.
- **Same ray-traced engine:** our access channel **is** LunaTwin (Sionna RT),
  aligned in solver configuration; it inherits LunaCov's 0.5 dB analytic agreement
  and ITU-R P.526 / terrestrial-measurement validation.
- **Complementary role:** LunaCov answers *where coverage exists* (placement,
  masts, node count, coverage boundaries) over the 10 km tile; LunaBridge takes a
  **traverse through one of those boundaries** and shows *what the live 5G + DTN
  system does* as the UE crosses the cliff — the operational consequence of
  LunaCov's coverage map.
- **Realises LunaCov's future work:** LunaCov's hardware-in-the-loop target
  (USRP/LCHEM) and its MATLAB link level become, in LunaBridge, a **live srsRAN
  5G stack + DTN** fed by the same channel, closing the loop end to end.

In the LunaBridge paper, §II is updated to report this (it was previously scoped
as future work): the measured LOLA channel now drives the live radio, with the
access-realism result in hand and the methodology cited to LunaCov.
