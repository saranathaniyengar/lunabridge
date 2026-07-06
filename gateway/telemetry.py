"""
gateway/telemetry.py

LunaBridge per-bundle telemetry data model and append-only JSONL writer.
This is the data model the scheduler (Days 16-28) is built AGAINST, so the
queue is never retrofitted to the metrics. It is importable offline; it needs
no running 5G stack, no network, and no µD3TN.

WHAT THIS MODULE IS NOT
-----------------------
This module does NOT encode bundles for the wire. BPv7 CBOR encoding
(RFC 8949) belongs to the µD3TN handoff, not here. Everything below is
internal bookkeeping in float epoch seconds (time.time()) and newline-
delimited JSON.

RFC 9171 CONSTRAINTS REFLECTED HERE
-----------------------------------
- Bundle lifetime is carried on the wire as MILLISECONDS past creation time,
  as a CBOR unsigned integer (RFC 9171 Sec. 4.3.1). Internally LunaBridge
  works in SECONDS (traffic.py default_ttl_s). The seconds->milliseconds
  conversion happens at BUNDLE CREATION / µD3TN handoff, NOT in this module.
- On expiry, RFC 9171 Sec. 4.3.1 says a node "SHOULD" delete the bundle --
  SHOULD, not MUST. So TerminalState.TTL_EXPIRED is a LunaBridge gateway
  POLICY decision to drop, not a normative protocol requirement.
- BPv7's primary block (RFC 9171 Sec. 4.3.1 field list, which is exhaustive)
  has NO in-band priority field. Priority is enforced only by the gateway
  queue and is never stamped into the bundle. traffic_class below is internal
  state, not a wire field.
- expiration_ts is computed from ingress_ts (see set_ttl). That approximates
  the bundle CREATION time with the N6 INGRESS time. Acceptable for this
  prototype; wrong for production. A clock-degraded lunar node would instead
  carry the Bundle Age extension block (RFC 9171 Sec. 4.4.2) -- note this in
  the paper; do not implement it in the prototype.

Source notes for component lineage:
  - TerminalState member names: LunaBridge labels for brief-implied concepts.
    No external standard defines these names.
  - BundleRecord lifetime handling: RFC 9171 Sec. 4.3.1 / Sec. 4.4.2.
    - mission_utility weights: read from traffic.py CLASS_SPECS (single source
      of truth). Day 11.5: traffic.py reduced from 7 classes to 4
      (COMMAND_CONTROL, OAM, SCIENCE_METADATA removed). Formula reduces to
      U = 100E + 10T + 1Sb - 1000E_exp (Sm term permanently zero).
      Weights are NOT re-derived here.
  - SchedulerSnapshot / SchedulingDecision fields: LunaBridge design from the
    brief's telemetry requirements (Sec. 7 of SESSION_STATE). Internal only.
  - JSONL format: standard practice, no RFC needed.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Iterable, Optional

from .traffic import TrafficClass, CLASS_SPECS


# ---------------------------------------------------------------------------
# Terminal state
# ---------------------------------------------------------------------------
class TerminalState(Enum):
    """Lifecycle outcome of a bundle.

    These names are LunaBridge labels for brief-implied concepts; no external
    standard defines them.

    PENDING is the only NON-terminal state. The other four are terminal: once
    a record is in one of them, mark() refuses to move it again.

    The distinction between TTL_EXPIRED and NEVER_SCHEDULED is the STARVATION
    SIGNAL and is the whole point of this enum for the forwarding-policy
    comparison:
      - TTL_EXPIRED    : the bundle WAS considered/queued but its deadline
                         passed before it could be sent (deadline loss).
      - NEVER_SCHEDULED: the contact window closed and the scheduler never
                         even selected this bundle (starvation loss). Under a
                         strict-priority policy, low-priority traffic dies this
                         way while high-priority traffic is delivered. Folding
                         this into TTL_EXPIRED would make starvation invisible.
      - QUEUE_OVERFLOW : dropped at admission because the buffer was full.
    """
    PENDING         = "pending"
    DELIVERED       = "delivered"
    TTL_EXPIRED     = "ttl_expired"
    QUEUE_OVERFLOW  = "queue_overflow"
    NEVER_SCHEDULED = "never_scheduled"


# Every terminal state except PENDING.
_TERMINAL_STATES = frozenset(
    s for s in TerminalState if s is not TerminalState.PENDING
)


def is_terminal(state: TerminalState) -> bool:
    return state in _TERMINAL_STATES


# ---------------------------------------------------------------------------
# Per-bundle record
# ---------------------------------------------------------------------------
@dataclass
class BundleRecord:
    """One row per bundle, tracking its full lifecycle.

    Required at construction (known at N6 ingress):
        bundle_id, flow_id, traffic_class, size_bytes, ingress_ts

    Filled in as the lifecycle progresses (None until then):
        created_ts          : bundle created / handed to BPv7 layer
        queue_admission_ts  : admitted to the scheduler queue
        transmission_ts     : first byte put on the link during a contact
        delivery_ts         : delivery confirmed (set by mark(DELIVERED))
        expiration_ts       : computed by set_ttl(); see RFC note in module
                              docstring (approximated from ingress_ts)
        contact_id          : the contact window this bundle was sent in

    All timestamps are float epoch seconds (time.time()).
    """
    bundle_id: str
    flow_id: str
    traffic_class: TrafficClass
    size_bytes: int
    ingress_ts: float

    created_ts: Optional[float] = None
    queue_admission_ts: Optional[float] = None
    transmission_ts: Optional[float] = None
    delivery_ts: Optional[float] = None
    expiration_ts: Optional[float] = None

    terminal_state: TerminalState = TerminalState.PENDING
    contact_id: Optional[str] = None

    # -- derived properties -------------------------------------------------
    @property
    def delivered(self) -> bool:
        return self.terminal_state is TerminalState.DELIVERED

    @property
    def queueing_delay_s(self) -> Optional[float]:
        """Time spent in the queue before transmission began.
        None until both queue_admission_ts and transmission_ts are set."""
        if self.queue_admission_ts is None or self.transmission_ts is None:
            return None
        return self.transmission_ts - self.queue_admission_ts

    @property
    def end_to_end_latency_s(self) -> Optional[float]:
        """N6 ingress to confirmed delivery. None unless delivered."""
        if self.delivery_ts is None:
            return None
        return self.delivery_ts - self.ingress_ts

    # -- mutators -----------------------------------------------------------
    def set_ttl(self, ttl_s: Optional[float] = None) -> None:
        """Set expiration_ts.

        If ttl_s is None, use the class default from traffic.py CLASS_SPECS
        (in SECONDS). expiration_ts = ingress_ts + ttl_s.

        NOTE (RFC 9171 Sec. 4.3.1 / 4.4.2): on the wire, lifetime is
        MILLISECONDS; the *1000 conversion happens at bundle creation, not
        here. And this uses ingress_ts as a proxy for creation time --
        acceptable for the prototype, wrong for production (which would use
        the Bundle Age extension block).
        """
        if ttl_s is None:
            ttl_s = CLASS_SPECS[self.traffic_class].default_ttl_s
        self.expiration_ts = self.ingress_ts + ttl_s

    def mark(self, new_state: TerminalState, ts: Optional[float] = None) -> None:
        """Transition to a terminal state.

        Raises RuntimeError if this record is ALREADY terminal -- double
        marking is a scheduler bug, not a silent no-op, so it must be loud.
        Raises ValueError if asked to mark PENDING (not a terminal state).

        If new_state is DELIVERED, delivery_ts is stamped (defaults to now).
        """
        if new_state is TerminalState.PENDING:
            raise ValueError("mark() requires a terminal state, not PENDING")
        if is_terminal(self.terminal_state):
            raise RuntimeError(
                f"bundle {self.bundle_id} already terminal "
                f"({self.terminal_state.value}); refusing to re-mark as "
                f"{new_state.value}"
            )
        if ts is None:
            ts = time.time()
        if new_state is TerminalState.DELIVERED and self.delivery_ts is None:
            self.delivery_ts = ts
        self.terminal_state = new_state

    # -- serialization ------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["traffic_class"] = self.traffic_class.value
        d["terminal_state"] = self.terminal_state.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "BundleRecord":
        d = dict(d)
        d["traffic_class"] = TrafficClass(d["traffic_class"])
        d["terminal_state"] = TerminalState(d["terminal_state"])
        return cls(**d)


# ---------------------------------------------------------------------------
# Scheduler-side records (internal design; build the model now, exporter later)
# ---------------------------------------------------------------------------
@dataclass
class SchedulerSnapshot:
    """Point-in-time scheduler/queue state. Per-class dicts are keyed by
    TrafficClass.value. LunaBridge design from the brief's telemetry reqs."""
    ts: float
    contact_id: Optional[str] = None
    queue_depth_by_class: dict = field(default_factory=dict)
    bytes_queued_by_class: dict = field(default_factory=dict)
    bytes_transmitted_by_class: dict = field(default_factory=dict)
    contact_window_start: Optional[float] = None
    contact_window_end: Optional[float] = None
    link_up: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SchedulingDecision:
    """A single scheduler decision, for tracing why a bundle was/wasn't sent.
    action e.g. 'transmit' | 'defer' | 'drop'. LunaBridge internal design."""
    ts: float
    bundle_id: str
    traffic_class: str          # store the .value; decisions are a flat log
    action: str
    reason: str = ""
    contact_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Append-only JSONL writer
# ---------------------------------------------------------------------------
class TelemetryWriter:
    """Writes three append-only JSONL files plus config.json under run_dir.

    Files:
        bundles.jsonl    -- one BundleRecord per line
        scheduler.jsonl  -- one SchedulerSnapshot per line
        decisions.jsonl  -- one SchedulingDecision per line
        config.json      -- the experiment config (single JSON object)

    JSONL contract: exactly one complete JSON object per line, newline-
    delimited, append-only (json.dumps(record) + "\n"). Each write opens the
    file in append mode and closes it, so a crash can never truncate a prior
    line.
    """

    BUNDLES = "bundles.jsonl"
    SCHEDULER = "scheduler.jsonl"
    DECISIONS = "decisions.jsonl"
    CONFIG = "config.json"

    def __init__(self, run_dir: str, config: Optional[dict] = None):
        self.run_dir = run_dir
        os.makedirs(run_dir, exist_ok=True)
        if config is not None:
            with open(os.path.join(run_dir, self.CONFIG), "w") as f:
                json.dump(config, f, indent=2, sort_keys=True)

    def _append(self, filename: str, obj: dict) -> None:
        line = json.dumps(obj, sort_keys=True)
        with open(os.path.join(self.run_dir, filename), "a") as f:
            f.write(line + "\n")

    def write_bundle(self, record: BundleRecord) -> None:
        self._append(self.BUNDLES, record.to_dict())

    def write_snapshot(self, snapshot: SchedulerSnapshot) -> None:
        self._append(self.SCHEDULER, snapshot.to_dict())

    def write_decision(self, decision: SchedulingDecision) -> None:
        self._append(self.DECISIONS, decision.to_dict())


def read_bundles(run_dir: str) -> list[BundleRecord]:
    """Read bundles.jsonl back into BundleRecord objects."""
    path = os.path.join(run_dir, TelemetryWriter.BUNDLES)
    out: list[BundleRecord] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(BundleRecord.from_dict(json.loads(line)))
    return out


# ---------------------------------------------------------------------------
# Analysis helpers  (operate on any iterable of BundleRecord, incl. read-back)
# ---------------------------------------------------------------------------
def delivery_ratio_by_class(records: Iterable[BundleRecord]) -> dict[str, float]:
    """delivered / total, per class. Keyed by TrafficClass.value. Only classes
    that actually appear in `records` are included (no division by zero)."""
    total: dict[str, int] = {}
    delivered: dict[str, int] = {}
    for r in records:
        k = r.traffic_class.value
        total[k] = total.get(k, 0) + 1
        if r.delivered:
            delivered[k] = delivered.get(k, 0) + 1
    return {k: delivered.get(k, 0) / total[k] for k in total}


def mission_utility(records: Iterable[BundleRecord]) -> float:
    """Mission utility U.

    Reading 2 (confirmed): U = sum of utility_weight (from CLASS_SPECS) over
    every DELIVERED bundle, minus 1000 for each EMERGENCY bundle that expired
    (TTL_EXPIRED).

    Day 11.5: traffic.py reduced from 7 to 4 classes (COMMAND_CONTROL, OAM,
    SCIENCE_METADATA removed -- see traffic.py). Brief's original formula
        U = 100E + 10T + 3Sm + 1Sb - 1000 E_expired
    can no longer be reproduced exactly -- Sm is permanently zero. Actual:
        U = 100E + 10T + 1Sb - 1000 E_expired

    The -1000 penalty is EMERGENCY + TTL_EXPIRED only -- the brief specifies a
    penalty for expired emergencies, not for other classes and not for
    starvation. Emergency bundles lost to NEVER_SCHEDULED / QUEUE_OVERFLOW are
    reported separately by starvation_summary(); they are not folded into U.
    """
    records = list(records)
    u = 0.0
    for r in records:
        if r.delivered:
            u += CLASS_SPECS[r.traffic_class].utility_weight
    expired_emergencies = sum(
        1 for r in records
        if r.traffic_class is TrafficClass.EMERGENCY
        and r.terminal_state is TerminalState.TTL_EXPIRED
    )
    u -= 1000.0 * expired_emergencies
    return u


def starvation_summary(records: Iterable[BundleRecord]) -> dict:
    """Distinguish deadline loss (TTL_EXPIRED) from starvation
    (NEVER_SCHEDULED) -- they are reported as SEPARATE counts because that
    distinction is the forwarding-policy comparison's headline signal.
    Also breaks each down by class."""
    ttl_expired = 0
    never_scheduled = 0
    queue_overflow = 0
    ttl_by_class: dict[str, int] = {}
    never_by_class: dict[str, int] = {}
    for r in records:
        k = r.traffic_class.value
        if r.terminal_state is TerminalState.TTL_EXPIRED:
            ttl_expired += 1
            ttl_by_class[k] = ttl_by_class.get(k, 0) + 1
        elif r.terminal_state is TerminalState.NEVER_SCHEDULED:
            never_scheduled += 1
            never_by_class[k] = never_by_class.get(k, 0) + 1
        elif r.terminal_state is TerminalState.QUEUE_OVERFLOW:
            queue_overflow += 1
    return {
        "ttl_expired": ttl_expired,
        "never_scheduled": never_scheduled,
        "queue_overflow": queue_overflow,
        "ttl_expired_by_class": ttl_by_class,
        "never_scheduled_by_class": never_by_class,
    }


# ---------------------------------------------------------------------------
# Offline smoke test
# ---------------------------------------------------------------------------
def _smoke_test() -> None:
    import tempfile

    t0 = 1_000_000.0  # fixed fake epoch so assertions are deterministic

    # b1: EMERGENCY, delivered
    b1 = BundleRecord("b1", "flowA", TrafficClass.EMERGENCY, 84, ingress_ts=t0)
    b1.set_ttl()                       # expiration_ts = t0 + EMERGENCY's default_ttl_s
    b1.queue_admission_ts = t0 + 1
    b1.transmission_ts = t0 + 2
    b1.mark(TerminalState.DELIVERED, ts=t0 + 5)

    # b2: TELEMETRY, never scheduled (window closed first) -> starvation
    b2 = BundleRecord("b2", "flowB", TrafficClass.TELEMETRY, 512, ingress_ts=t0)
    b2.set_ttl()
    b2.mark(TerminalState.NEVER_SCHEDULED)

    # b3: EMERGENCY, TTL expired -> deadline loss + the -1000 penalty
    b3 = BundleRecord("b3", "flowC", TrafficClass.EMERGENCY, 84, ingress_ts=t0)
    b3.set_ttl()
    b3.mark(TerminalState.TTL_EXPIRED)

    records = [b1, b2, b3]

    with tempfile.TemporaryDirectory() as run_dir:
        w = TelemetryWriter(run_dir, config={"policy": "fifo", "link_rate_bps": 2_000_000})
        for r in records:
            w.write_bundle(r)
        # also exercise the scheduler-side writers
        w.write_snapshot(SchedulerSnapshot(ts=t0, contact_id="c1", link_up=True))
        w.write_decision(SchedulingDecision(ts=t0 + 2, bundle_id="b1",
                                            traffic_class="emergency",
                                            action="transmit", reason="rank0"))

        # read back from disk -> verify round-trip + analysis on reloaded data
        loaded = read_bundles(run_dir)
        assert len(loaded) == 3
        assert {r.bundle_id for r in loaded} == {"b1", "b2", "b3"}

        u = mission_utility(loaded)
        starv = starvation_summary(loaded)
        ddr = delivery_ratio_by_class(loaded)

    # U = +100 (b1 delivered emergency) - 1000 (b3 expired emergency) = -900
    assert u == -900.0, f"expected U=-900.0, got {u}"

    # starvation: TTL_EXPIRED and NEVER_SCHEDULED tracked SEPARATELY
    assert starv["ttl_expired"] == 1, starv
    assert starv["never_scheduled"] == 1, starv
    # the two loss modes live in SEPARATE buckets -- collapsing them would
    # hide starvation, which is exactly what this enum exists to prevent.
    assert starv["ttl_expired_by_class"] != starv["never_scheduled_by_class"], starv
    assert starv["ttl_expired_by_class"] == {"emergency": 1}, starv
    assert starv["never_scheduled_by_class"] == {"telemetry": 1}, starv
    assert starv["queue_overflow"] == 0, starv

    # delivery ratio: emergency 1/2 delivered, telemetry 0/1
    assert ddr["emergency"] == 0.5, ddr
    assert ddr["telemetry"] == 0.0, ddr

    # derived properties on the reloaded delivered bundle
    b1L = next(r for r in loaded if r.bundle_id == "b1")
    assert b1L.delivered is True
    assert b1L.end_to_end_latency_s == 5.0, b1L.end_to_end_latency_s
    assert b1L.queueing_delay_s == 1.0, b1L.queueing_delay_s
    assert b1L.expiration_ts == t0 + CLASS_SPECS[TrafficClass.EMERGENCY].default_ttl_s

    # mark() must REFUSE to re-mark an already-terminal bundle
    raised = False
    try:
        b1.mark(TerminalState.TTL_EXPIRED)
    except RuntimeError:
        raised = True
    assert raised, "mark() did not raise on double-mark"

    # mark(PENDING) must be rejected
    raised = False
    try:
        BundleRecord("b4", "f", TrafficClass.MEDIA, 1, ingress_ts=t0).mark(TerminalState.PENDING)
    except ValueError:
        raised = True
    assert raised, "mark(PENDING) did not raise"

    print("smoke test PASSED")
    print(f"  mission_utility = {u}")
    print(f"  delivery_ratio_by_class = {ddr}")
    print(f"  starvation_summary = {starv}")


if __name__ == "__main__":
    _smoke_test()
