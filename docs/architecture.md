# LunaBridge architecture

## System overview

    UE (rover) ──5G NR──► gNB ──N1/N2/N3──► Open5GS 5GC
                                                  │ N6
                                         LunaBridge gateway
                                           (NFQUEUE intercept)
                                                  │ BPv7
                                           µD3TN moon node
                                                  │
                                         Link controller
                                        (blackout simulator)
                                                  │ (contact open)
                                          µD3TN relay node
                                                  │ BPv7
                                          µD3TN earth node (DSN)

## Docker networks

| Network       | Subnet         | Purpose                          |
|---------------|----------------|----------------------------------|
| 5g-surface    | 10.10.0.0/24   | UERANSIM + Open5GS + gateway     |
| lunar-space   | 10.20.0.0/24   | moon node ↔ relay node           |
| deep-space    | 10.30.0.0/24   | relay node ↔ earth node          |
| observability | 10.40.0.0/24   | Prometheus + Grafana             |

## N6 interception method: iptables NFQUEUE

Rule inserted in Open5GS UPF container after UPF starts:

    iptables -I FORWARD -i ogstun -j NFQUEUE --queue-num 0

Gateway runs Python netfilterqueue, receives every UE packet,
classifies by DSCP, wraps in BPv7 bundle, sends to µD3TN AAP2.

## Gateway FSM

    NOMINAL ──CONTACT_WINDOW_END──► BLACKOUT
    BLACKOUT ──CONTACT_WINDOW_START──► RESTORING
    RESTORING ──QUEUE_EMPTY──► NOMINAL
    BLACKOUT ──GAP > 1h──► DEEP_SLEEP

*Stub — full implementation Day 16–22.*
