#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
"""
channel/sionna_rt/lunar_rt_poc.py

Sionna RT proof-of-concept for the LUNAR SURFACE access channel.

Why Sionna RT and not an NTN channel model (TR 38.811 / OpenNTN): those model
EARTH NTN effects (atmospheric absorption, iono/tropo scintillation, land-mobile-
satellite clutter) that DO NOT EXIST on the Moon. The lunar surface link is
governed by the TERRAIN -- diffraction over crater rims, two-ray ground bounce,
and hard geometric shadowing. Sionna RT ray-traces exactly that physics over a
real 3D surface, so it is the physically correct, reproducible tool, and it does
NOT need the live srsRAN testbed.

This POC builds a synthetic crater-rim scene (the `build_terrain` interface is
LOLA-ready: swap the heightfield for a real PGDA LOLA 5 m/px DEM patch), places a
fixed gNB and sweeps a rover along a line from line-of-sight, across the rim, into
geometric shadow, and ray-traces the channel at each position with specular
ground reflection + edge diffraction. Output: path loss vs rover position --
the "cliff, not a fade" the paper argues for, from first-principles propagation.

Run:  python lunar_rt_poc.py --out figs
"""
from __future__ import annotations
import argparse, os
import numpy as np
import trimesh

import sionna.rt as rt
from sionna.rt import (load_scene, SceneObject, RadioMaterial, PlanarArray,
                       Transmitter, Receiver, PathSolver)


# --------------------------------------------------------------------------- #
# Terrain. Synthetic crater rim now; same signature takes a real DEM later.
# --------------------------------------------------------------------------- #
def crater_rim_heightfield(nx=161, ny=41, dx=1.25, dy=1.25,
                           rim_x=120.0, rim_h=12.0, rim_w=7.0):
    """A flat regolith plain with one sharp crater-rim ridge at x=rim_x.
    Returns (verts[N,3], faces[M,3]) in metres. Replace the `z` line with a
    LOLA DEM patch (z = dem[j, i]) to ray-trace real south-pole terrain."""
    xs = np.arange(nx) * dx
    ys = (np.arange(ny) - ny // 2) * dy
    X, Y = np.meshgrid(xs, ys)                       # [ny, nx]
    # sharp triangular ridge (crater rim) -> real apex EDGE for UTD diffraction
    Z = np.maximum(0.0, rim_h * (1.0 - np.abs(X - rim_x) / rim_w))
    verts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float64)
    faces = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a = j * nx + i; b = a + 1; c = a + nx; d = c + 1
            faces.append([a, c, b]); faces.append([b, c, d])
    return verts, np.asarray(faces, dtype=np.int64)


def build_terrain_ply(path, **kw):
    v, f = crater_rim_heightfield(**kw)
    mesh = trimesh.Trimesh(vertices=v, faces=f, process=False)
    mesh.fix_normals()
    mesh.export(path)
    return path, float(v[:, 0].max()), float(kw.get("rim_x", 120.0)), \
        float(kw.get("rim_h", 12.0))


# --------------------------------------------------------------------------- #
def regolith_material(freq_hz, eps_r=None, loss_tangent=None, rho=1.5):
    """Lunar regolith dielectric, IDENTICAL model to the companion lunaremu
    physics (so the RT channel and the analytical channel share inputs):
      - real permittivity  eps' = 1.919**rho       (density law; rho=1.5 -> 2.658)
      - loss tangent        tan d = 10**(0.312*rho + f_GHz**0.069 - 3.79)
                                                    (Siegler et al. 2020, S-band ~0.0055)
      - conductivity        sigma = 2*pi*f * eps0 * eps' * tan d
    Pass eps_r/loss_tangent to override."""
    if eps_r is None:
        eps_r = 1.919 ** rho
    if loss_tangent is None:
        loss_tangent = 10 ** (0.312 * rho + (freq_hz / 1e9) ** 0.069 - 3.79)
    eps0 = 8.8541878128e-12
    sigma = 2 * np.pi * freq_hz * eps0 * eps_r * loss_tangent
    return RadioMaterial(name="regolith", relative_permittivity=eps_r,
                         conductivity=sigma), sigma


def path_gain_db(paths):
    """Total path gain (dB) per RX: sum |a|^2 over all paths/antennas/time."""
    a, _ = paths.cir(normalize_delays=False, out_type="numpy")  # complex array
    a = np.asarray(a)
    # collapse everything except the RX axis (axis 0)
    g = np.sum(np.abs(a) ** 2, axis=tuple(range(1, a.ndim)))     # [num_rx]
    npaths = np.sum(np.abs(a) > 0, axis=tuple(range(1, a.ndim)))
    print(f"paths/RX: {int(npaths.min())}-{int(npaths.max())} "
          f"({int(np.sum(npaths == 0))} fully-shadowed positions)")
    g = np.where(g > 0, g, np.nan)
    return 10.0 * np.log10(g)                                    # gain dB (neg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="figs")
    ap.add_argument("--freq", type=float, default=2.5e9, help="S-band (paper)")
    ap.add_argument("--gnb-x", type=float, default=0.0)
    ap.add_argument("--gnb-h", type=float, default=6.0, help="gNB mast height m")
    ap.add_argument("--rover-h", type=float, default=1.5)
    ap.add_argument("--n-pos", type=int, default=60)
    ap.add_argument("--max-depth", type=int, default=3)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    ply, x_max, rim_x, rim_h = build_terrain_ply(os.path.join(a.out, "terrain.ply"))
    print(f"terrain: plain 0..{x_max:.0f} m, crater rim at x={rim_x:.0f} m, "
          f"h={rim_h:.0f} m")

    scene = load_scene()                 # empty scene
    scene.frequency = a.freq
    mat, sigma = regolith_material(a.freq)
    print(f"regolith: eps_r=3.0, sigma={sigma:.4g} S/m @ {a.freq/1e9:.2f} GHz")

    terrain = SceneObject(fname=ply, name="terrain", radio_material=mat)
    scene.add(terrain)

    # isotropic, vertically polarised single elements (omni S-band terminals)
    scene.tx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso",
                                 polarization="V")
    scene.rx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso",
                                 polarization="V")

    # fixed gNB near the start, on a mast
    tx = Transmitter(name="gnb", position=[a.gnb_x, 0.0, a.gnb_h])
    scene.add(tx)

    # rover sweeps from LOS, across the rim, into shadow
    x0, x1 = 20.0, x_max - 10.0
    xs = np.linspace(x0, x1, a.n_pos)
    for k, x in enumerate(xs):
        scene.add(Receiver(name=f"rover_{k}", position=[float(x), 0.0, a.rover_h]))

    solver = PathSolver()
    paths = solver(scene, max_depth=a.max_depth, los=True,
                   specular_reflection=True, diffraction=True,
                   edge_diffraction=True, refraction=False)
    pg_db = path_gain_db(paths)
    pl_db = -pg_db                        # path loss (dB)

    # free-space reference for context
    c = 299792458.0
    d = np.sqrt((xs - a.gnb_x) ** 2 + (a.gnb_h - a.rover_h) ** 2)
    fspl = 20 * np.log10(4 * np.pi * d * a.freq / c)

    # report + save data
    np.savez(os.path.join(a.out, "lunar_rt_poc.npz"), x=xs, pl_db=pl_db,
             fspl=fspl, rim_x=rim_x)
    los_mask = xs < rim_x
    print(f"\nLOS stretch  (x<{rim_x:.0f}): PL {np.nanmin(pl_db[los_mask]):.0f}"
          f"..{np.nanmax(pl_db[los_mask]):.0f} dB")
    print(f"shadow (x>{rim_x:.0f}): PL {np.nanmin(pl_db[~los_mask]):.0f}"
          f"..{np.nanmax(pl_db[~los_mask]):.0f} dB")
    # the cliff: jump across the rim
    i_rim = int(np.argmin(np.abs(xs - rim_x)))
    if 0 < i_rim < len(xs) - 1:
        step = np.nanmax(pl_db[i_rim:i_rim + 5]) - np.nanmin(pl_db[max(0, i_rim - 5):i_rim + 1])
        print(f"cliff at rim: +{step:.1f} dB step across the crater rim")

    _plot(xs, pl_db, fspl, rim_x, a.out)


def _plot(xs, pl_db, fspl, rim_x, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(5.0, 3.1))
    ax.plot(xs, pl_db, color="#c0392b", lw=1.9, label="Sionna RT (terrain)")
    ax.plot(xs, fspl, color="#888", lw=1.0, ls="--", label="free space")
    ax.axvline(rim_x, color="#2c3e50", ls=":", lw=1.2)
    ax.text(rim_x + 1, ax.get_ylim()[0] + 3, " crater rim", fontsize=7,
            color="#2c3e50", rotation=90, va="bottom")
    ax.set_xlabel("rover distance from gNB (m)")
    ax.set_ylabel("path loss (dB)")
    ax.set_title("Lunar surface access channel — Sionna RT ray tracing", fontsize=9)
    ax.legend(fontsize=7, loc="lower right"); ax.grid(alpha=0.3)
    ax.invert_yaxis()
    fig.tight_layout()
    p = os.path.join(out, "lunar_rt_poc.pdf")
    fig.savefig(p, bbox_inches="tight")
    fig.savefig(p.replace(".pdf", ".png"), dpi=150, bbox_inches="tight")
    print("wrote", p)


if __name__ == "__main__":
    main()
