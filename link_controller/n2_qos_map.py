#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
"""
link_controller/n2_qos_map.py

Build the TEID -> 5QI table the N6 bridge uses, from an N2 (NGAP) capture of
the PDU session set-up: in each PDU Session Resource Setup Request the AMF
sends the gNB the UPF's uplink tunnel endpoint (UL NG-U gTP-TEID) together
with the QoS flows of the session (QFI, 5QI). Uplink G-PDUs then carry that
TEID, so TEID (+QFI) identifies the QoS flow and hence its 5QI.

Usage: python3 n2_qos_map.py <n2.pcap> <map.json>      (needs tshark)
"""
import json, subprocess, sys


def main(pcap, out):
    rows = subprocess.run(
        ["tshark", "-r", pcap, "-Y", "ngap.procedureCode == 29 && ngap.fiveQI",
         "-T", "fields", "-E", "separator=|", "-e", "ngap.gTP_TEID",
         "-e", "ngap.qosFlowIdentifier", "-e", "ngap.fiveQI"],
        capture_output=True, text=True, check=True).stdout.split("\n")
    qmap = {}
    for r in rows:
        if not r.strip():
            continue
        teids, qfis, fiveqis = (x.split(",") for x in r.split("|"))
        teid = int(teids[0].replace(":", ""), 16)     # UL NG-U TEID (UPF side)
        qmap[teid] = int(fiveqis[0])
        print(f"TEID 0x{teid:08x}  QFI {qfis[0]}  5QI {fiveqis[0]}")
    json.dump(qmap, open(out, "w"))
    if not qmap:
        sys.exit("no PDU Session Resource Setup with a 5QI found")


if __name__ == "__main__":
    main(*sys.argv[1:3])
