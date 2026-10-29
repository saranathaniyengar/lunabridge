# SPDX-License-Identifier: GPL-3.0
"""
link_controller/live_gateway.py

The gateway scheduler running LIVE behind the N6 -> BPv7 bridge. Bundles that
the bridge classifies (from the 5QI) are admitted into a bounded buffer and
drained during contacts, at the contact's rate, in policy order. It applies the
same rules as gateway/scheduler.py (offline replay), bundle by bundle in real
time:

  admission  drop-tail       : an arrival that does not fit is dropped;
             priority-aware  : evict strictly lower-rank queued bundles (newest
                               first, lowest rank first) to make room, else drop;
  drain      fifo            : arrival order, class-blind;
             strict          : lowest rank first, FIFO within a rank;
             wfq             : EMERGENCY first (hard preempt), then
                               work-conserving deficit round robin over
                               TELEMETRY:SCIENCE:MEDIA = 10:1:1 (Shreedhar &
                               Varghese), quantum in bytes.

Lifetime: the gateway holds bundles across the gap, so it enforces each class's
(compressed) lifetime: a bundle older than its lifetime when it reaches the head
is discarded as expired. Thread-safe: the bridge thread admits, the drain loop
pops.
"""
from __future__ import annotations
import collections, threading

RANK = {"emergency": 0, "telemetry": 1, "sci_bulk": 2, "media": 3}
WFQ_WEIGHT = {"telemetry": 10.0, "sci_bulk": 1.0, "media": 1.0}
WFQ_QUANTUM = 1500.0     # bytes added per unit weight per DRR round


class LiveGateway:
    def __init__(self, policy: str, admission: str, buffer_bytes: int,
                 lifetimes: dict, log=None):
        assert policy in ("fifo", "strict", "wfq") and admission in ("droptail", "priority")
        self.policy, self.admission, self.cap = policy, admission, buffer_bytes
        self.L = lifetimes
        self.q = {c: collections.deque() for c in RANK}      # per-class FIFOs
        self.fifo = collections.deque()                       # global order (fifo drain)
        self.bytes = 0
        self.deficit = {c: 0.0 for c in WFQ_WEIGHT}
        self.rr = list(WFQ_WEIGHT)                            # DRR visiting order
        self.lock = threading.Lock()
        self.log = log or (lambda ev, b, t: None)

    # -- admission -------------------------------------------------------
    def admit(self, b: dict, now: float) -> bool:
        """b: {'seq','cls','n','t', 'payload'}; n is the bundle's size as charged
        both in the buffer and on the link."""
        with self.lock:
            if self.bytes + b["n"] > self.cap:
                # purge expired bundles first: within a class they share one
                # lifetime and arrive in order, so they form each queue's head
                for c in RANK:
                    self._head(c, now)
            if self.bytes + b["n"] > self.cap and self.admission == "priority":
                for c in sorted(RANK, key=RANK.get, reverse=True):     # lowest rank first
                    if RANK[c] <= RANK[b["cls"]]:
                        break
                    while self.q[c] and self.bytes + b["n"] > self.cap:
                        v = self.q[c].pop()                            # newest of that class
                        v["gone"] = True
                        self.bytes -= v["n"]
                        self.log("evict", v, now)
            if self.bytes + b["n"] > self.cap:
                self.log("drop", b, now)
                return False
            b["gone"] = False
            self.q[b["cls"]].append(b)
            self.fifo.append(b)
            self.bytes += b["n"]
            return True

    # -- drain -----------------------------------------------------------
    def _head(self, c, now):
        """Head of class c after discarding expired / evicted bundles."""
        dq = self.q[c]
        while dq:
            h = dq[0]
            if h["gone"]:
                dq.popleft(); continue
            if now - h["t"] > self.L[c]:
                dq.popleft(); h["gone"] = True; self.bytes -= h["n"]
                self.log("expire", h, now); continue
            return h
        return None

    def _take(self, c):
        b = self.q[c].popleft()
        b["gone"] = True
        self.bytes -= b["n"]
        return b

    def next(self, now: float):
        """Pop the next bundle to transmit, or None if the buffer is empty."""
        with self.lock:
            if self.policy == "fifo":
                while self.fifo:
                    b = self.fifo[0]
                    if b["gone"]:
                        self.fifo.popleft(); continue
                    h = self._head(b["cls"], now)          # also expires stale heads
                    if h is b:
                        self.fifo.popleft()
                        return self._take(b["cls"])
                    self.fifo.popleft()            # b expired (or inconsistent): skip
                    continue
                return None
            if self._head("emergency", now) is not None:  # strict and wfq: preempt
                return self._take("emergency")
            if self.policy == "strict":
                for c in ("telemetry", "sci_bulk", "media"):
                    if self._head(c, now) is not None:
                        return self._take(c)
                return None
            # wfq: work-conserving DRR
            backlogged = [c for c in self.rr if self._head(c, now) is not None]
            if not backlogged:
                for c in self.deficit:
                    self.deficit[c] = 0.0
                return None
            for _ in range(10_000):
                c = self.rr[0]
                h = self._head(c, now)
                if h is None:
                    self.deficit[c] = 0.0
                    self.rr.append(self.rr.pop(0)); continue
                if self.deficit[c] >= h["n"]:
                    self.deficit[c] -= h["n"]
                    return self._take(c)
                self.deficit[c] += WFQ_QUANTUM * WFQ_WEIGHT[c]
                self.rr.append(self.rr.pop(0))
            return None

    def snapshot(self):
        with self.lock:
            return {c: [b for b in dq if not b["gone"]] for c, dq in self.q.items()}
