#!/usr/bin/env python3
"""
Export the lunaremu Connecting Ridge (Site01) scenario geometry for Sionna RT.

Runs in lunaremu's own venv (needs rasterio/pyproj). Builds the exact scenario
the paper uses -- gNB at the tile's highest point, UE3 a radial "cliff" rover
traverse over the real PGDA LOLA 5 m/px DEM -- and saves the clipped DEM plus
gNB/rover pixel positions to an .npz that the Sionna RT script reads.

Usage (from the lunaremu repo root):
  python export_lola_scene.py <lunaremu_dir> <out.npz>
"""
import os, sys, tomllib
import numpy as np

lun_dir = sys.argv[1] if len(sys.argv) > 1 else "."
out = sys.argv[2] if len(sys.argv) > 2 else "lola_scene.npz"
os.chdir(lun_dir)
from lunaremu.cli import build_scenario

cfg = tomllib.load(open("scenarios/connecting_ridge_4node.toml", "rb"))
sc, info = build_scenario(cfg, data_dir="data")

dem = np.asarray(sc.dem, dtype=np.float64)        # [ny, nx] elevation (m)
px = float(sc.pixel_size_m)
freq = float(sc.freq_hz)

def rc(node, k):
    if hasattr(node, "at"):
        return tuple(node.at(k))
    p = node.positions
    return tuple(p[k] if not node.static else p[0] if isinstance(p[0], (list, tuple)) else p)

gnb = sc.nodes[0]
grow, gcol = rc(gnb, 0)
g_h = float(gnb.height_m)

rover = [n for n in sc.nodes if "rover" in n.name.lower()][0]
rows, cols = [], []
for k in range(sc.n_snapshots):
    r, c = rc(rover, k)
    rows.append(int(r)); cols.append(int(c))
rows = np.array(rows); cols = np.array(cols)
r_h = float(rover.height_m)

# unique consecutive positions (1 m/s on a 5 m grid repeats each pixel ~5x)
keep = np.concatenate([[True], (np.diff(rows) != 0) | (np.diff(cols) != 0)])
rows_u, cols_u = rows[keep], cols[keep]

np.savez(out, dem=dem, px=px, freq=freq,
         gnb_row=int(grow), gnb_col=int(gcol), gnb_h=g_h,
         rover_rows=rows_u, rover_cols=cols_u, rover_h=r_h)
print(f"saved {out}: DEM {dem.shape} @ {px} m/px, freq {freq/1e9:.2f} GHz")
print(f"gNB px=({grow},{gcol}) h={g_h} m, elev={dem[int(grow),int(gcol)]:.1f} m")
print(f"rover: {len(rows_u)} unique px positions, "
      f"from ({rows_u[0]},{cols_u[0]}) to ({rows_u[-1]},{cols_u[-1]})")
