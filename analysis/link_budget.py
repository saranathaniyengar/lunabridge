"""
analysis/link_budget.py

Surface-to-relay link budget of Table I (omnidirectional S-band terminal to an
LCRNS ELFO relay), from the TRUE slant range between the landing site and the
relay during every contact of the 1-SV plan.

Slant range: the GMAT ephemeris of SV1 (Moon-centred EME2000, 5 min steps,
ISL-topology/phase1-lcrns-coverage/gmat_outputs/ephem_SV1.oem) minus the site
position (Malapert Peak, 86 S 0 E, IAU_MOON, rotated to J2000 with SPICE
pck00010), sampled inside the contacts of gateway/lcrns_relay_contact_plan_1sv.csv.
The derived per-sample series is versioned in
channel/data/slant_range_malapert_sv1.csv.gz, so the budget runs without SPICE:

    python3 analysis/link_budget.py                    # from the versioned CSV
    python3 analysis/link_budget.py --oem <ephem_SV1.oem> --kernels <dir>  # re-derive

Noise convention: N = k * 250 K * B, plus the 2.5 dB receiver noise figure.
"""
from __future__ import annotations
import argparse, csv, gzip, math, os, re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.path.join(HERE, "gateway", "lcrns_relay_contact_plan_1sv.csv")
SERIES = os.path.join(HERE, "channel", "data", "slant_range_malapert_sv1.csv.gz")

F_HZ, BW_HZ = 2.5e9, 20e6
EIRP_DBM, G_RX_DBI = 23.0, 0.0
T_K, NF_DB = 250.0, 2.5
C = 299_792_458.0
K_DBM = -198.6                 # Boltzmann constant, dBm/K/Hz
R_MOON_KM = 1737.4
SITE_LAT, SITE_LON = -86.0, 0.0


def snr_db(d_km: float) -> float:
    fspl = 20 * math.log10(4 * math.pi * d_km * 1e3 * F_HZ / C)
    noise = K_DBM + 10 * math.log10(T_K) + 10 * math.log10(BW_HZ) + NF_DB
    return EIRP_DBM + G_RX_DBI - fspl - noise


def derive(oem: str, kernels: str) -> list[tuple[float, float, float, int]]:
    import datetime as dt
    import numpy as np
    import spiceypy as sp
    sp.furnsh(os.path.join(kernels, "naif0012.tls"))
    sp.furnsh(os.path.join(kernels, "pck00010.tpc"))
    lat, lon = math.radians(SITE_LAT), math.radians(SITE_LON)
    up = np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])
    plan = list(csv.DictReader(open(PLAN)))
    t0 = dt.datetime(2027, 3, 1)
    out = []
    for line in open(oem):
        p = line.split()
        if len(p) != 7 or not re.match(r"\d{4}-", p[0]):
            continue
        sec = (dt.datetime.strptime(p[0][:19], "%Y-%m-%dT%H:%M:%S") - t0).total_seconds()
        m = sp.pxform("IAU_MOON", "J2000", sp.str2et(p[0]))
        u = m @ up
        d = np.array(list(map(float, p[1:4]))) - R_MOON_KM * u
        rng = float(np.linalg.norm(d))
        el = math.degrees(math.asin(float(d @ u) / rng))
        inc = int(any(float(r["start_sec"]) <= sec <= float(r["end_sec"]) for r in plan))
        out.append((sec, rng, el, inc))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--oem"); ap.add_argument("--kernels")
    a = ap.parse_args()
    if a.oem:
        rows = derive(a.oem, a.kernels)
        with gzip.open(SERIES, "wt") as f:
            w = csv.writer(f); w.writerow(["t_s", "slant_km", "elev_deg", "in_contact"])
            for r in rows:
                w.writerow([int(r[0]), round(r[1], 1), round(r[2], 2), r[3]])
        print(f"wrote {SERIES} ({len(rows)} samples)")
    rows = [r for r in csv.DictReader(gzip.open(SERIES, "rt")) if r["in_contact"] == "1"]
    d = sorted(float(r["slant_km"]) for r in rows)
    el = [float(r["elev_deg"]) for r in rows]
    lo, hi, med = d[0], d[-1], d[len(d) // 2]
    print(f"in-contact samples {len(d)}; slant {lo:.0f}-{hi:.0f} km (median {med:.0f}); "
          f"elevation {min(el):.1f}-{max(el):.1f} deg")
    for name, x in (("min", lo), ("median", med), ("max", hi)):
        s = snr_db(x)
        print(f"  {name:6s} {x:7.0f} km: SNR {s:6.1f} dB; EIRP for SNR=0 "
              f"{EIRP_DBM - s:5.1f} dBm ({10 ** ((EIRP_DBM - s - 30) / 10) / 1e3:.0f} kW); "
              f"with a 30 dBi relay aperture {s + 30:6.1f} dB")


if __name__ == "__main__":
    main()
