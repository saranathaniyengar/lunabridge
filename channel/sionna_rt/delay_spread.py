#!/usr/bin/env python3
"""
channel/sionna_rt/delay_spread.py

RMS delay spread of the Sionna-RT lunar access channel along the Connecting
Ridge rover traverse, used to justify a single-tap (frequency-flat) channel in
the live loop. Same scene and solver as lunar_rt_lola.py (LunaTwin-aligned:
spherical-cap curvature, Siegler regolith, LOS + specular + edge diffraction,
3 interactions, refraction off).

Usage: python delay_spread.py --scene lola_scene.npz --stride 3 \
           --out data/delay_spread.json
"""
import argparse, json
import numpy as np
from sionna.rt import (load_scene, SceneObject, PlanarArray, Transmitter,
                       Receiver, PathSolver)
from lunar_rt_poc import regolith_material
from lunar_rt_lola import mesh_from_patch, local_xyz


def rms_ds(a, tau):
    p = np.abs(a) ** 2
    m = p > 0
    if not m.any():
        return None
    p, tau = p[m], tau[m]
    tbar = np.sum(p * tau) / np.sum(p)
    return float(np.sqrt(np.sum(p * (tau - tbar) ** 2) / np.sum(p)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="lola_scene.npz")
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--margin-px", type=int, default=40)
    ap.add_argument("--max-depth", type=int, default=3)
    ap.add_argument("--out", default="data/delay_spread.json")
    a = ap.parse_args()

    s = np.load(a.scene)
    dem, px, f = s["dem"].astype(float), float(s["px"]), float(s["freq"])
    R = 1737400.0
    jj, ii = np.meshgrid(np.arange(dem.shape[0]), np.arange(dem.shape[1]), indexing="ij")
    dem = dem - (((ii - dem.shape[1] / 2) * px) ** 2 + ((jj - dem.shape[0] / 2) * px) ** 2) / (2 * R)
    gr, gc, gh = int(s["gnb_row"]), int(s["gnb_col"]), float(s["gnb_h"])
    rr, rc, rh = s["rover_rows"][::a.stride], s["rover_cols"][::a.stride], float(s["rover_h"])
    allr, allc = np.r_[gr, rr], np.r_[gc, rc]
    r0, r1 = max(0, allr.min() - a.margin_px), min(dem.shape[0], allr.max() + a.margin_px)
    c0, c1 = max(0, allc.min() - a.margin_px), min(dem.shape[1], allc.max() + a.margin_px)
    mesh, z0 = mesh_from_patch(dem, px, r0, r1, c0, c1)
    ply = "figs/_delay_spread_patch.ply"; mesh.export(ply)

    sc = load_scene(); sc.frequency = f
    mat, _ = regolith_material(f)
    sc.add(SceneObject(fname=ply, name="terrain", radio_material=mat))
    sc.tx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")
    sc.rx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")
    sc.add(Transmitter("gnb", position=local_xyz(gr, gc, dem, r0, c0, px, z0, gh)))
    for k in range(len(rr)):
        sc.add(Receiver(f"r{k}", position=local_xyz(rr[k], rc[k], dem, r0, c0, px, z0, rh)))
    paths = PathSolver()(sc, max_depth=a.max_depth, los=True, specular_reflection=True,
                         diffraction=True, edge_diffraction=True, refraction=False)
    A, T = paths.cir(normalize_delays=True, out_type="numpy")
    A, T = np.asarray(A), np.asarray(T)
    dist = np.sqrt((rr - gr) ** 2.0 + (rc - gc) ** 2.0) * px
    rows = []
    for k in range(A.shape[0]):
        ak = A[k].ravel()
        tk = np.asarray(T[k]).ravel()[:ak.shape[0]] if T.ndim > 1 else T.ravel()[:ak.shape[0]]
        ds = rms_ds(ak, tk)
        rows.append({"ground_range_m": float(dist[k]), "rms_delay_spread_s": ds})
    ds_all = [r["rms_delay_spread_s"] for r in rows if r["rms_delay_spread_s"] is not None]
    out = {"positions": len(rows), "positions_with_paths": len(ds_all),
           "median_s": float(np.median(ds_all)), "max_s": float(np.max(ds_all)),
           "nr_cp_30khz_s": 2.34e-6, "per_position": rows,
           "config": {"max_depth": a.max_depth, "stride": a.stride,
                      "regolith": "eps'=1.919^1.5, Siegler 2020 tan d", "freq_hz": f}}
    json.dump(out, open(a.out, "w"), indent=1)
    print(f"{len(ds_all)}/{len(rows)} positions with paths; RMS delay spread "
          f"median {out['median_s']*1e9:.2f} ns, max {out['max_s']*1e9:.2f} ns -> {a.out}")


if __name__ == "__main__":
    main()
