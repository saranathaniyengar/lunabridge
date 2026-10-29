"""
analysis/live_bridge_table.py

Table of the live N6 -> BPv7 bridge runs: the class is read by the bridge from
TEID -> 5QI (NGAP), and bundles are carried by uD3TN over the compressed LCRNS
slice. Two experiments:
  * joint  (runs/live_5qi/joint, link_controller/run_bridge_joint.sh): four
    UEs, one per class, all four flows at the same time;
  * alone  (runs/live_5qi/<class>, link_controller/run_bridge_class.sh): one
    UE, one class per run.
Writes LaTeX rows: class & 5QI & rho & bundles (joint) & joint & alone & law.

Usage: python3 analysis/live_bridge_table.py [--runs runs/live_5qi] [--out rows.tex]
"""
import argparse, json, os

ORDER = ["emergency", "telemetry", "sci_bulk", "media"]
NAME = {"emergency": r"\textsc{Emergency}", "telemetry": r"\textsc{Telemetry}",
        "sci_bulk": r"\textsc{Science}", "media": r"\textsc{Media}"}


def load(path, cls):
    rows = [x for x in json.load(open(path)) if x["cls"] == cls]
    return rows[0] if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs/live_5qi")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lines = []
    for cls in ORDER:
        j = load(os.path.join(a.runs, "joint", "results.json"), cls)
        s = load(os.path.join(a.runs, cls, "results.json"), cls)
        print(f"{cls:10s} 5QI {j['fiveqi']} QFI {j['qfi']} L {j['L']} s rho {j['rho']:.2f} | "
              f"joint {j['delivered']}/{j['sent']} = {j['measured']:.4f} | "
              f"alone {s['delivered']}/{s['sent']} = {s['measured']:.4f} | "
              f"law {j['closed_form']:.4f}")
        n = f"{j['sent']:,}".replace(",", "{,}")
        lines.append(f"{NAME[cls]} & {j['fiveqi']} & {j['rho']:.2f} & {n} & "
                     f"{j['measured']:.3f} & {s['measured']:.3f} & {j['closed_form']:.3f} \\\\")
    if a.out:
        open(a.out, "w").write("\n".join(lines) + "\n")
        print("wrote", a.out)


if __name__ == "__main__":
    main()
