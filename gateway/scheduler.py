"""
gateway/scheduler.py

Day 12 -- scheduler v1: strict-priority + FIFO-within-rank bundle admission
and transmission scheduling against a ContactPlan.

Decisions locked this session (Day 12), not assumed:
  - Fixture source for tests: the real LCRNS 1-satellite degraded contact
    plan (lcrns_relay_contact_plan_1sv.csv), not Day 10's synthetic
    fixtures -- chosen specifically because the 5-satellite file's merged
    coverage has effectively zero gap and can't exercise blackout-driven
    outcomes at all.
  - custody_required: INERT in v1. RFC 9171 removed BPv6's custody-transfer
    mechanism from the Bundle Protocol's core spec; no in-band wire
    mechanism exists for it without adopting a not-yet-standardized
    extension. No admission/scheduling logic in this module reads
    custody_required. Revisit only if/when Day 16+ DTN work adopts such
    an extension.
  - MEDIA's expendable=True: NO special-case code. MEDIA competes normally
    at rank 3, FIFO-within-rank like every other class. Its TTL (11813s)
    is the shortest of all four classes, so under real contention it
    naturally resolves to TTL_EXPIRED without needing a dedicated code
    path -- that IS what "expendable" means in practice here.
  - Non-fit policy: SKIP-OVER (not block-on-non-fit). A bundle that does
    not fit the remaining bit budget this window is passed over --
    smaller/lower-rank bundles behind it may still transmit this window.
    Logged as a non-terminal 'defer' decision. The standardized fix for
    this class of problem is BPv7 bundle fragmentation (RFC 9171); that is
    explicitly Day 16+ scope, not implemented here -- bundles are atomic
    in v1.
  - Queue: BOUNDED. max_queue_bytes is a required constructor argument,
    not a hardcoded default -- this project has no traffic-arrival-rate
    model yet, so any specific number belongs in the caller/test, sized
    per real DTN practice (ION sizes heap storage as an absolute byte
    quantity budgeted for worst-case backlog, not a percentage of a
    contact window -- see ION Design & Operation Manual). Document the
    reasoning for whatever value is chosen at the call site.

WHAT THIS MODULE DELIBERATELY DOES NOT DO (Day 14+ / Day 16+ scope):
  - No WFQ, no queue_budget enforcement (ClassSpec.queue_budget is read
    from traffic.py but never consulted here -- Day 14).
  - No bundle fragmentation (Day 16+, needs BPv7/CBOR work this session
    explicitly excludes).
  - No µD3TN/DTN handoff of any kind.

Per-window algorithm, walking ContactPlan via its PUBLIC interface only
(next_after / active_at -- this module does not reach into ContactPlan's
internals):
  1. Expire-in-blackout: any admitted, still-PENDING bundle whose
     expiration_ts <= window.start_ts is marked TTL_EXPIRED before
     anything else happens for this window.
  2. Admission: not-yet-admitted bundles with ingress_ts <= window.start_ts
     attempt admission. If admitting would push total active queued bytes
     over max_queue_bytes, the bundle is marked QUEUE_OVERFLOW instead
     (dropped at admission, per TerminalState's own documented meaning).
  3. Eligibility + strict-priority sort: admitted, PENDING bundles are
     sorted by (CLASS_SPECS[traffic_class].rank, ingress_ts) --
     strict-priority, FIFO within a rank.
  4. Drain against window.raw_bit_budget(), skip-over on non-fit.
  5. After the last window closes: one final expire-in-blackout pass at
     that window's end_ts, then anything STILL pending is marked
     NEVER_SCHEDULED -- the contact plan ran out before the scheduler
     ever got to select it (starvation loss, per TerminalState's own
     docstring on why this must not be folded into TTL_EXPIRED).
"""

from __future__ import annotations

from typing import List, Optional

from .contact_plan import ContactPlan, ContactWindow
from .telemetry import BundleRecord, SchedulingDecision, TerminalState
from .traffic import CLASS_SPECS


class Scheduler:
    """
    Strict-priority, FIFO-within-rank scheduler v1.

    max_queue_bytes: REQUIRED, no default. See module docstring -- this
    project has no arrival-rate model yet, so this project does not
    invent a "reasonable default" here. Callers must size and justify it.
    """

    def __init__(self, plan: ContactPlan, max_queue_bytes: float):
        if max_queue_bytes <= 0:
            raise ValueError(
                f"max_queue_bytes ({max_queue_bytes}) must be > 0; a "
                f"zero-or-negative cap can never admit anything, which is "
                f"not a meaningful scheduler configuration."
            )
        self.plan = plan
        self.max_queue_bytes = max_queue_bytes
        self.decisions: List[SchedulingDecision] = []

        # Active queue: bundles that are admitted and still PENDING.
        self._active: List[BundleRecord] = []
        self._active_bytes = 0

    # -- decision logging -----------------------------------------------
    def _log(self, ts: float, bundle: BundleRecord, action: str,
              reason: str, contact_id: Optional[str] = None) -> None:
        self.decisions.append(SchedulingDecision(
            ts=ts,
            bundle_id=bundle.bundle_id,
            traffic_class=bundle.traffic_class.value,
            action=action,
            reason=reason,
            contact_id=contact_id,
        ))

    # -- admission --------------------------------------------------------
    def _try_admit(self, bundle: BundleRecord, ts: float) -> None:
        """Attempt admission. Marks QUEUE_OVERFLOW immediately if the
        buffer cap would be exceeded; otherwise stamps queue_admission_ts
        and adds to the active queue."""
        if self._active_bytes + bundle.size_bytes > self.max_queue_bytes:
            bundle.mark(TerminalState.QUEUE_OVERFLOW, ts=ts)
            self._log(ts, bundle, action="drop", reason="queue_overflow")
            return
        bundle.queue_admission_ts = ts
        self._active.append(bundle)
        self._active_bytes += bundle.size_bytes

    def _admit_arrivals(self, pending_arrivals: List[BundleRecord],
                         cutoff_ts: float) -> List[BundleRecord]:
        """Admit (or overflow) everything with ingress_ts <= cutoff_ts.
        Returns the remaining not-yet-arrived bundles."""
        ready = [b for b in pending_arrivals if b.ingress_ts <= cutoff_ts]
        still_future = [b for b in pending_arrivals if b.ingress_ts > cutoff_ts]
        # Admit strictly in ingress order so admission itself doesn't
        # silently re-prioritize arrivals (priority is applied at drain
        # time, not at admission time).
        for b in sorted(ready, key=lambda b: b.ingress_ts):
            self._try_admit(b, ts=b.ingress_ts)
        return still_future

    # -- expiry -----------------------------------------------------------
    def _expire_in_blackout(self, cutoff_ts: float) -> None:
        """Mark TTL_EXPIRED (and remove from the active queue) any admitted
        PENDING bundle whose expiration_ts <= cutoff_ts."""
        still_active: List[BundleRecord] = []
        for b in self._active:
            if b.terminal_state is TerminalState.PENDING and b.expiration_ts is not None \
                    and b.expiration_ts <= cutoff_ts:
                b.mark(TerminalState.TTL_EXPIRED, ts=cutoff_ts)
                self._active_bytes -= b.size_bytes
                self._log(cutoff_ts, b, action="drop", reason="ttl_expired")
            else:
                still_active.append(b)
        self._active = still_active

    # -- drain --------------------------------------------------------------
    def _drain_window(self, window: ContactWindow) -> None:
        eligible = [
            b for b in self._active
            if b.terminal_state is TerminalState.PENDING
            and b.ingress_ts < window.end_ts
        ]
        eligible.sort(key=lambda b: (CLASS_SPECS[b.traffic_class].rank, b.ingress_ts))

        remaining_bits = window.raw_bit_budget()
        for b in eligible:
            bits_needed = b.size_bytes * 8
            if bits_needed <= remaining_bits:
                b.transmission_ts = window.start_ts
                b.contact_id = window.contact_id
                b.mark(TerminalState.DELIVERED, ts=window.start_ts)
                remaining_bits -= bits_needed
                self._active_bytes -= b.size_bytes
                self._log(window.start_ts, b, action="transmit",
                           reason=f"rank{CLASS_SPECS[b.traffic_class].rank}",
                           contact_id=window.contact_id)
            else:
                # SKIP-OVER: doesn't fit *this* window; stays queued for
                # the next one. Non-terminal -- not a drop.
                self._log(window.start_ts, b, action="defer",
                           reason="insufficient_budget",
                           contact_id=window.contact_id)

        self._active = [b for b in self._active
                         if b.terminal_state is TerminalState.PENDING]

    # -- top-level run ------------------------------------------------------
    def run(self, bundles: List[BundleRecord]) -> None:
        """
        Run every bundle in `bundles` through the full plan. Each bundle
        MUST already have expiration_ts set (via BundleRecord.set_ttl())
        before being passed in -- this is loud, not silent, if violated.

        On return, every bundle's terminal_state is one of DELIVERED,
        TTL_EXPIRED, QUEUE_OVERFLOW, or NEVER_SCHEDULED.
        """
        for b in bundles:
            if b.expiration_ts is None:
                raise ValueError(
                    f"bundle {b.bundle_id}: expiration_ts is None -- call "
                    f"set_ttl() before passing bundles to Scheduler.run(). "
                    f"This is a caller bug, not something the scheduler "
                    f"should silently paper over."
                )

        pending_arrivals = list(bundles)
        ts = float("-inf")
        window = self.plan.next_after(ts)
        last_end_ts = None

        while window is not None:
            pending_arrivals = self._admit_arrivals(pending_arrivals, window.end_ts)
            self._expire_in_blackout(window.start_ts)
            self._drain_window(window)
            last_end_ts = window.end_ts
            window = self.plan.next_after(window.end_ts)

        # Anything that arrives after the last window closes can never be
        # scheduled either -- admit it (for bookkeeping/overflow purposes)
        # then it will fall into the same final NEVER_SCHEDULED sweep.
        if last_end_ts is not None:
            self._admit_arrivals(pending_arrivals, float("inf"))
            self._expire_in_blackout(last_end_ts)
        else:
            # Plan had zero windows -- everything that could be admitted
            # is admitted (for accurate overflow accounting), nothing can
            # ever be scheduled.
            self._admit_arrivals(pending_arrivals, float("inf"))

        # Final sweep: contact plan is exhausted. Anything still PENDING
        # was never selected by the scheduler -- starvation, not deadline
        # loss. Must NOT be folded into TTL_EXPIRED.
        for b in self._active:
            if b.terminal_state is TerminalState.PENDING:
                b.mark(TerminalState.NEVER_SCHEDULED)
                self._log(last_end_ts if last_end_ts is not None else 0.0,
                           b, action="drop", reason="never_scheduled")
        self._active = []
