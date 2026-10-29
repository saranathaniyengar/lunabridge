#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
"""
channel/sionna_rt/lunar_rt_lola.py

Sionna RT over the REAL Connecting Ridge terrain (PGDA LOLA 5 m/px, Site01) --
the physically-grounded version of the POC in lunar_rt_poc.py.

Geometry comes from lunaremu (export_lola_scene.py -> lola_scene.npz): the exact
gNB (tile-max, 30 m mast) and UE3 radial "cliff" rover traverse the paper uses.
This script crops a terrain patch around the gNB+rover corridor, meshes it,
assigns a regolith material, and ray-traces the channel at every rover position
with specular ground reflection + edge diffraction -- replacing lunaremu's
analytical Deygout/two-ray model with full ray tracing.

Output: path loss vs rover ground range over the real crater terrain.

Run:  python lunar_rt_lola.py --scene lola_scene.npz --out figs
"""
from __future__ import annotations
import argparse, os
import numpy as np
import trimesh

import sionna.rt as rt
from sionna.rt import (load_scene, SceneObject, PlanarArray,
                       Transmitter, Receiver, PathSolver)
from lunar_rt_poc import regolith_material, path_gain_db


def mesh_from_patch(dem, px, r0, r1, c0, c1):
    """Heightfield mesh of dem[r0:r1, c0:c1]; local origin at (r0,c0), z zeroed
    to the patch minimum. Returns (mesh, z0) with z0 the elevation offset (m)."""
    sub = dem[r0:r1, c0:c1]
    ny, nx = sub.shape
    z0 = float(np.nanmin(sub))
    xs = np.arange(nx) * px
    ys = np.arange(ny) * px
    X, Y = np.meshgrid(xs, ys)
    Z = sub - z0
    verts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float64)
    faces = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a = j * nx + i; b = a + 1; c = a + nx; d = c + 1
            faces.append([a, c, b]); faces.append([b, c, d])
    m = trimesh.Trimesh(vertices=verts, faces=np.asarray(faces, np.int64),
                        process=False)
    m.fix_normals()
    return m, z0


def local_xyz(row, col, dem, r0, c0, px, z0, h):
    return [float((col - c0) * px), float((row - r0) * px),
            float(dem[int(row), int(col)] - z0 + h)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="lola_scene.npz")
    ap.add_argument("--out", default="figs")
    ap.add_argument("--margin-px", type=int, default=40)
    ap.add_argument("--max-depth", type=int, default=3)  # == LunaTwin (3 interactions)
    ap.add_argument("--stride", type=int, default=2, help="rover subsample")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    s = np.load(a.scene)
    dem, px, freq = s["dem"], float(s["px"]), float(s["freq"])
    grow, gcol, g_h = int(s["gnb_row"]), int(s["gnb_col"]), float(s["gnb_h"])
    rrows, rcols, r_h = s["rover_rows"], s["rover_cols"], float(s["rover_h"])
    rrows, rcols = rrows[::a.stride], rcols[::a.stride]

    # Spherical-cap curvature, IDENTICAL to LunaCov/LunaTwin: lower each DEM
    # sample by ((x-xc)^2+(y-yc)^2)/(2 R_moon) about the 10 km tile centre, so the
    # mesh and the devices see the same lunar horizon as the companion planner.
    R_MOON = 1737400.0
    jj, ii = np.meshgrid(np.arange(dem.shape[0]), np.arange(dem.shape[1]), indexing="ij")
    xc, yc = dem.shape[1] / 2.0, dem.shape[0] / 2.0
    dem = dem - (((ii - xc) * px) ** 2 + ((jj - yc) * px) ** 2) / (2.0 * R_MOON)

    # patch bbox around gNB + rover corridor
    allr = np.concatenate([[grow], rrows]); allc = np.concatenate([[gcol], rcols])
    r0 = max(0, int(allr.min()) - a.margin_px); r1 = min(dem.shape[0], int(allr.max()) + a.margin_px)
    c0 = max(0, int(allc.min()) - a.margin_px); c1 = min(dem.shape[1], int(allc.max()) + a.margin_px)
    print(f"patch rows[{r0}:{r1}] cols[{c0}:{c1}] = {r1-r0}x{c1-c0} px "
          f"({(r1-r0)*(c1-c0)} verts), {freq/1e9:.2f} GHz")

    mesh, z0 = mesh_from_patch(dem, px, r0, r1, c0, c1)
    ply = os.path.join(a.out, "connecting_ridge.ply"); mesh.export(ply)

    scene = load_scene(); scene.frequency = freq
    mat, sigma = regolith_material(freq)   # Siegler 2020 / density law (== lunaremu)
    print(f"regolith eps'=2.658 tan d=0.0055 sigma={sigma:.4g} S/m; gNB elev {dem[grow,gcol]:.0f} m +{g_h} m mast")
    scene.add(SceneObject(fname=ply, name="terrain", radio_material=mat))
    scene.tx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")
    scene.rx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")

    scene.add(Transmitter(name="gnb", position=local_xyz(grow, gcol, dem, r0, c0, px, z0, g_h)))
    for k in range(len(rrows)):
        scene.add(Receiver(name=f"rover_{k}",
                           position=local_xyz(rrows[k], rcols[k], dem, r0, c0, px, z0, r_h)))

    paths = PathSolver()(scene, max_depth=a.max_depth, los=True,
                         specular_reflection=True, diffraction=True,
                         edge_diffraction=True, refraction=False)
    pl_db = -path_gain_db(paths)

    # rover ground range from gNB (m)
    dist = np.sqrt((rrows - grow) ** 2.0 + (rcols - gcol) ** 2.0) * px
    c = 299792458.0
    fspl = 20 * np.log10(4 * np.pi * dist * freq / c)
    np.savez(os.path.join(a.out, "lunar_rt_lola.npz"), dist=dist, pl_db=pl_db, fspl=fspl)

    ok = np.isfinite(pl_db)
    print(f"RT path loss over real terrain: {np.nanmin(pl_db[ok]):.0f}..{np.nanmax(pl_db[ok]):.0f} dB; "
          f"{int(np.sum(~ok))}/{len(pl_db)} fully-shadowed positions")

    # Emit the pathloss-trace JSON that grc_lunar_standalone.py --pathloss-trace
    # consumes -> the SAME RT result drives the live srsRAN broker. Shadow (RT
    # outage) becomes an explicit outage flag; speed 1 m/s so t ~ ground range.
    import json
    OUT_PL = 200.0  # dB written for outage positions (effectively dark)
    trace = []
    for k in range(len(dist)):
        shadow = not np.isfinite(pl_db[k])
        trace.append({"t": float(dist[k]),            # 1 m/s traverse
                      "pl_db": [OUT_PL if shadow else float(pl_db[k])],
                      "outage": [1 if shadow else 0]})
    jp = os.path.join(a.out, "connecting_ridge.pathloss.json")
    json.dump(trace, open(jp, "w"))
    print(f"wrote {jp} ({len(trace)} snaps) -> drive the live broker with "
          f"grc_lunar_standalone.py --pathloss-trace {os.path.basename(jp)}")

    _plot(dist, pl_db, fspl, a.out)


def _plot(dist, pl_db, fspl, out):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(5.4, 3.2))
    fin = np.isfinite(pl_db)
    ax.plot(dist, fspl, color="#888", lw=1.0, ls="--", label="free space")
    ax.plot(dist[fin], pl_db[fin], color="#c0392b", lw=1.8, marker="o", ms=2.6,
            label="Sionna RT (real LOLA terrain)")
    # shadow / outage region: from the cliff onward
    if (~fin).any():
        cliff = dist[np.argmax(~fin)]
        lo, hi = np.nanmin(pl_db[fin]) - 2, np.nanmax(pl_db[fin]) + 8
        ax.axvspan(cliff, dist.max(), color="#2c3e50", alpha=0.10)
        ax.axvline(cliff, color="#2c3e50", ls=":", lw=1.3)
        ax.text(cliff + 6, hi - 1.5, "coverage cliff →\nterrain shadow (RT outage)",
                fontsize=7, color="#2c3e50", va="top")
        ax.set_ylim(hi, lo)   # inverted (path loss down)
    else:
        ax.invert_yaxis()
    ax.set_xlabel("rover ground range from gNB (m)")
    ax.set_ylabel("path loss (dB)")
    ax.set_title("Connecting Ridge access channel — Sionna RT on PGDA LOLA 5 m/px",
                 fontsize=8.5)
    ax.legend(fontsize=7, loc="upper left"); ax.grid(alpha=0.3)
    fig.tight_layout()
    p = os.path.join(out, "lunar_rt_lola.pdf")
    fig.savefig(p, bbox_inches="tight"); fig.savefig(p.replace(".pdf", ".png"), dpi=150, bbox_inches="tight")
    print("wrote", p)


if __name__ == "__main__":
    main()
