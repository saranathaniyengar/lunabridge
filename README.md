# LunaBridge

A working prototype of a lunar 5G-to-DTN gateway bridging
3GPP 5G NR with CCSDS Bundle Protocol v7.

LunaBridge intercepts N6 traffic from a UERANSIM-simulated lunar
rover, adapts IP packets into BPv7 bundles, and delivers them
across simulated ELFO orbital contact windows to an Earth DSN node.

![Status](https://img.shields.io/badge/status-Day%205%20foundation-blue)

## Pipeline

UE (rover) → gNB → Open5GS 5GC → N6 → LunaBridge → BPv7 → µD3TN → Earth DSN

## Phase status

| Phase | Description                        | Status        |
|-------|------------------------------------|---------------|
| 0     | 5G core running (Open5GS+UERANSIM) |  Complete    |
| 1     | N6 interception (NFQUEUE)          |  Complete    |
| 2     | µD3TN integration (BPv7)           | 🔜 Day 11     |
| 3     | Blackout + FSM                     | 🔜 Day 16     |
| 4     | Priority queue + telemetry         | 🔜 Day 23     |
| 5     | Demo hardening                     | 🔜 Day 29     |

See [docs/architecture.md](docs/architecture.md) for system design.
