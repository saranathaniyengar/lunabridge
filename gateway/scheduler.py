"""
gateway/scheduler.py

Day 12 -- scheduler v1: strict-priority + FIFO-within-rank bundle admission
and transmission scheduling against a ContactPlan.

Day 14 -- adds SchedulingPolicy: a pluggable drain policy (FIFO /
STRICT_PRIORITY / WFQ / WFQ_SKIP_OVER), so all four can be run against the
SAME plan+traffic+queue-cap for a real comparison (see SESSION_STATE.md
Day 14). STRICT_PRIORITY remains the default -- every pre-Day-14 caller's
behavior is unchanged unless policy is explicitly set.

S2-W6-7 -- adds DEADLINE_AWARE (true rank-blind EDF), UTILITY_AWARE
(two-tier at-risk + utility-density), and UTILITY_PURE (ablation: Tier-2
logic alone, no at-risk gate -- bonus, isolates what the at-risk gate
actually buys). See each SchedulingPolicy member's docstring for full
reasoning; summary:
  - Verified before building: sorting purely by utility_weight collapses
    into STRICT_PRIORITY's exact ordering for this project's classes
    (utility-per-byte never inverts class rank), so UTILITY_AWARE is NOT
    "STRICT_PRIORITY with different numbers" -- its two real levers are
    (a) bin-packing at the margin (smaller lower-value bundles can fill
    gaps STRICT_PRIORITY leaves empty) and (b) the at-risk gate, which
    reasons about genuine "last chance" urgency, not blind deadline order.
  - DEADLINE_AWARE deliberately has NO EMERGENCY exception -- tests the
    honest cost of pure deadline-scheduling (it CAN let EMERGENCY expire
    for a lower-value, marginally-more-urgent bundle; this is the finding,
    not a bug).

Decisions locked Day 12, not assumed:
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
    at rank 3, FIFO-within-rank like every other class. Its TTL is the
    shortest of all four classes, so under real contention it naturally
    resolves to TTL_EXPIRED without needing a dedicated code path -- that
    IS what "expendable" means in practice here.
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

Decisions locked Day 14 (see SESSION_STATE.md for full sourcing):
  1. WFQ preserves EMERGENCY's hard-preempt (untouched from v1); WFQ
     applies only among TELEMETRY/SCIENCE_BULK/MEDIA for the remainder.
     This deliberately departs from Cisco LLQ's policer (LLQ caps and
     drops overflow from its priority queue -- rejected here since
     dropping EMERGENCY to protect fairness elsewhere is backwards for
     this taxonomy).
  2. queue_budget (traffic.py) is a WFQ scheduling weight, not a byte cap.
  3. Values: TELEMETRY:SCIENCE_BULK:MEDIA = 10:1:1. TELEMETRY sourced from
     utility_weight; SCIENCE_BULK:MEDIA=1:1 is an explicit policy floor,
     not derived (MEDIA's utility_weight=0.0 would zero its WFQ share).
  4. Live metrics = richer JSONL fields only (telemetry.py), no console
     stream -- this is a discrete-event batch simulation, not a live
     system; a 90-day run completes in well under a second, so there is
     nothing to meaningfully "watch live."
  5. All 5 Day 12/13 tests pass unmodified -- none of them exercise
     multi-class contention among TELEMETRY/SCIENCE_BULK/MEDIA, the only
     scenario WFQ actually changes. New tests added for that scenario
     specifically (test_scheduler.py).
  Mechanism: standard Deficit Round Robin (Shreedhar & Varghese 1996) --
  quantum = weight share of the post-EMERGENCY remainder, per-class
  deficit carries forward across windows (idle queues reset to 0, no
  hoarding), rotation of check order among the three classes each window
  to avoid positional bias when the window is oversubscribed.
  WFQ_SKIP_OVER is an experimental variant (NOT standard DRR, not backed
  by the DRR fairness proof): extends Day 12's skip-over philosophy into
  the deficit check itself, scanning past a non-fitting head-of-line
  bundle rather than ending that class's turn.

WHAT THIS MODULE DELIBERATELY DOES NOT DO (Day 16+ scope):
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
  3. Eligibility, then drain per self.policy (Day 14 / S2-W6-7):
       FIFO             -- ingress_ts order only, no class read at all.
       STRICT_PRIORITY   -- (rank, ingress_ts) order, v1 behavior, default.
       WFQ / WFQ_SKIP_OVER -- EMERGENCY hard-preempt, then DRR among the
                              rest (see Day 14 decisions above).
       DEADLINE_AWARE    -- pure EDF, expiration_ts order, class-blind.
       UTILITY_AWARE     -- at-risk bundles first (by utility_weight desc),
                            then everything else by utility-per-byte desc.
       UTILITY_PURE      -- utility-per-byte desc, no at-risk gate at all.
  4. Drain against window.raw_bit_budget(), skip-over on non-fit.
  5. After the last window closes: one final expire-in-blackout pass at
     that window's end_ts, then anything STILL pending is marked
     NEVER_SCHEDULED -- the contact plan ran out before the scheduler
     ever got to select it (starvation loss, per TerminalState's own
     docstring on why this must not be folded into TTL_EXPIRED).
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from .contact_plan import ContactPlan, ContactWindow
from .telemetry import BundleRecord, SchedulingDecision, TerminalState
from .traffic import CLASS_SPECS, TrafficClass


class SchedulingPolicy(Enum):
    """Day 14 / S2-W6-7: pluggable drain policy, added specifically so all
    six (plus one bonus ablation) can be run against the SAME
    plan+traffic+queue-cap for a real comparison (paper needs this, not
    just an assertion backed by literature). See SESSION_STATE.md / the
    S2-W6-7 conversation log for the fairness/starvation/value tradeoffs
    each one demonstrates.
    """
    FIFO = "fifo"
    # Fully naive -- zero class-awareness, not even EMERGENCY hard-preempt.
    # The deliberate floor baseline: never run in practice, exists to show
    # what un-prioritized scheduling looks like (a distress bundle CAN sit
    # behind trivial MEDIA traffic under this policy, by design).

    STRICT_PRIORITY = "strict_priority"
    # Day 12 v1, unchanged. Matches the actual DTN-native scheduling model
    # (ION's CLM strict-priority queues; BPv7 has no in-band priority field
    # at all -- see SESSION_STATE.md sourcing). DEFAULT, so every existing
    # caller's behavior is unchanged unless policy is explicitly set.

    WFQ = "wfq"
    # Day 14. EMERGENCY hard-preempts (Decision 1, untouched from v1);
    # TELEMETRY/SCIENCE_BULK/MEDIA fair-share the remainder via standard
    # Deficit Round Robin (Shreedhar & Varghese 1996) -- head-of-line
    # bundle failing its class's deficit ends that class's turn for the
    # round; deficit carries forward (idle queues reset to 0, no hoarding).

    WFQ_SKIP_OVER = "wfq_skip_over"
    # Day 14 experimental variant, NOT standard DRR -- extends Day 12's
    # skip-over philosophy (decision 4) into the deficit check itself: a
    # head-of-line bundle that fails deficit doesn't end the class's turn;
    # smaller bundles behind it in the same class's queue are still tried
    # against the same unconsumed deficit. No literature source claims this
    # preserves DRR's fairness bound -- flagged as our own variant for the
    # paper to evaluate, not attributed to Shreedhar & Varghese.

    DEADLINE_AWARE = "deadline_aware"
    # S2-W6-7. True Earliest-Deadline-First (Liu & Layland 1973): ignores
    # traffic_class entirely. At every step, serves whichever ELIGIBLE
    # bundle has the smallest expiration_ts, full stop. Deliberately has
    # NO EMERGENCY exception -- tests the honest cost of pure deadline
    # scheduling. This CAN let an EMERGENCY bundle expire in favor of a
    # lower-value bundle that happens to be marginally more urgent; that
    # is the intended finding (urgency-aware, value-blind), not a bug.
    # Since bundles are always drained smallest-expiration-first, if the
    # head of the sorted list hasn't expired, nothing behind it has either
    # -- no mid-loop re-check needed (expiry is already handled upstream
    # by _expire_in_blackout before this runs).

    UTILITY_AWARE = "utility_aware"
    # S2-W6-7. Two-tier, NOT a plain utility_weight sort (verified: a
    # plain weight sort collapses into STRICT_PRIORITY's exact ordering
    # for this project's classes, since utility-per-byte never inverts
    # class rank -- see module docstring). Tier 1 (mandatory): bundles
    # that are AT-RISK -- expiration_ts falls before the NEXT contact
    # window even opens (next_after(window.end_ts)), meaning skipping now
    # is a permanent loss, not a delay. Sorted by utility_weight
    # descending among themselves (save the highest-value ones if not all
    # fit). Tier 2 (opportunistic): everything else, sorted by
    # utility-per-byte (utility_weight / size_bytes) descending, greedy-
    # filling whatever capacity Tier 1 left behind -- the actual
    # bin-packing/knapsack step that STRICT_PRIORITY never performs (it
    # never looks at size at the margin at all). No explicit EMERGENCY
    # exception needed -- EMERGENCY's utility_weight (100) already
    # dominates Tier 1's sort naturally when it IS at-risk, without
    # hard-coding it, unlike WFQ's Decision 1.

    UTILITY_PURE = "utility_pure"
    # S2-W6-7, BONUS ablation (not part of the required six; build only
    # if time allows -- see conversation log). Utility-per-byte density
    # sorting ALONE, with NO at-risk gate at all. Exists specifically to
    # isolate how much of UTILITY_AWARE's behavior the at-risk gate
    # (Tier 1) is actually responsible for. Ignores expiry completely: a
    # high-density bundle with hours left is always sent before a
    # low-density bundle about to die, every time, by design -- this is
    # expected to behave close to STRICT_PRIORITY in practice (density
    # order rarely inverts class rank here), which is itself the point of
    # the ablation.


class Scheduler:
    """
    Pluggable-policy scheduler. Default policy is STRICT_PRIORITY (v1
    behavior, unchanged) so every pre-Day-14 caller is unaffected.

    max_queue_bytes: REQUIRED, no default. See module docstring -- this
    project has no arrival-rate model yet, so this project does not
    invent a "reasonable default" here. Callers must size and justify it.
    """

    _WFQ_CLASSES = [TrafficClass.TELEMETRY, TrafficClass.SCIENCE_BULK,
                     TrafficClass.MEDIA]

    def __init__(self, plan: ContactPlan, max_queue_bytes: float,
                 policy: SchedulingPolicy = SchedulingPolicy.STRICT_PRIORITY):
        if max_queue_bytes <= 0:
            raise ValueError(
                f"max_queue_bytes ({max_queue_bytes}) must be > 0; a "
                f"zero-or-negative cap can never admit anything, which is "
                f"not a meaningful scheduler configuration."
            )
        self.plan = plan
        self.max_queue_bytes = max_queue_bytes
        self.policy = policy
        self.decisions: List[SchedulingDecision] = []

        # Active queue: bundles that are admitted and still PENDING.
        self._active: List[BundleRecord] = []
        self._active_bytes = 0

        # WFQ-only state. Harmless (unused) for FIFO/STRICT_PRIORITY/
        # DEADLINE_AWARE/UTILITY_AWARE/UTILITY_PURE.
        self._deficit: Dict[TrafficClass, float] = {
            tc: 0.0 for tc in self._WFQ_CLASSES
        }
        self._rotation_index = 0

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

    # -- drain: dispatcher (Day 14 / S2-W6-7) --------------------------------
    def _drain_window(self, window: ContactWindow) -> None:
        eligible = [
            b for b in self._active
            if b.terminal_state is TerminalState.PENDING
            and b.ingress_ts < window.end_ts
        ]

        if self.policy is SchedulingPolicy.FIFO:
            self._drain_fifo(eligible, window)
        elif self.policy is SchedulingPolicy.STRICT_PRIORITY:
            self._drain_strict_priority(eligible, window)
        elif self.policy is SchedulingPolicy.WFQ:
            self._drain_wfq(eligible, window, skip_over_deficit=False)
        elif self.policy is SchedulingPolicy.WFQ_SKIP_OVER:
            self._drain_wfq(eligible, window, skip_over_deficit=True)
        elif self.policy is SchedulingPolicy.DEADLINE_AWARE:
            self._drain_edf(eligible, window)
        elif self.policy is SchedulingPolicy.UTILITY_AWARE:
            self._drain_utility_aware(eligible, window)
        elif self.policy is SchedulingPolicy.UTILITY_PURE:
            self._drain_utility_pure(eligible, window)
        else:
            raise ValueError(f"unknown scheduling policy: {self.policy}")

        self._active = [b for b in self._active
                         if b.terminal_state is TerminalState.PENDING]

    # -- shared drain primitive (S2-W6-7) ------------------------------------
    def _drain_ordered(self, ordered: List[BundleRecord], window: ContactWindow,
                        reason: str) -> None:
        """Shared by DEADLINE_AWARE / UTILITY_PURE / (per-tier in
        UTILITY_AWARE): given a list already sorted in the order this
        policy wants to try bundles, drain greedily against the window's
        bit budget, skip-over on non-fit (same non-terminal 'defer'
        philosophy as every other policy in this file)."""
        remaining_bits = window.raw_bit_budget()
        for b in ordered:
            bits_needed = b.size_bytes * 8
            if bits_needed <= remaining_bits:
                b.transmission_ts = window.start_ts
                b.contact_id = window.contact_id
                b.mark(TerminalState.DELIVERED, ts=window.start_ts)
                remaining_bits -= bits_needed
                self._active_bytes -= b.size_bytes
                self._log(window.start_ts, b, action="transmit",
                           reason=reason, contact_id=window.contact_id)
            else:
                self._log(window.start_ts, b, action="defer",
                           reason="insufficient_budget",
                           contact_id=window.contact_id)
        return remaining_bits

    # -- drain: STRICT_PRIORITY (Day 12 v1, unchanged logic) ---------------
    def _drain_strict_priority(self, eligible: List[BundleRecord],
                                window: ContactWindow) -> None:
        eligible.sort(key=lambda b: (CLASS_SPECS[b.traffic_class].rank, b.ingress_ts))
        self._drain_ordered(eligible, window, reason="strict_priority")

    # -- drain: FIFO (Day 14, fully naive) ----------------------------------
    def _drain_fifo(self, eligible: List[BundleRecord],
                     window: ContactWindow) -> None:
        """Zero class-awareness. Sort by ingress_ts only. Deliberate floor
        baseline -- see SchedulingPolicy.FIFO docstring."""
        ordered = sorted(eligible, key=lambda b: b.ingress_ts)
        self._drain_ordered(ordered, window, reason="fifo")

    # -- drain: DEADLINE_AWARE / EDF (S2-W6-7) -------------------------------
    def _drain_edf(self, eligible: List[BundleRecord],
                    window: ContactWindow) -> None:
        """True Earliest-Deadline-First (Liu & Layland 1973). Ignores
        traffic_class entirely -- sort key is expiration_ts only. No
        EMERGENCY exception, deliberately (see SchedulingPolicy docstring).
        """
        ordered = sorted(eligible, key=lambda b: b.expiration_ts)
        self._drain_ordered(ordered, window, reason="edf")

    # -- drain: UTILITY_AWARE (S2-W6-7) --------------------------------------
    def _drain_utility_aware(self, eligible: List[BundleRecord],
                              window: ContactWindow) -> None:
        """Two-tier utility-maximizing policy. See SchedulingPolicy.
        UTILITY_AWARE docstring for full reasoning -- summary: Tier 1
        (at-risk: expiration_ts before the next window even opens) is
        mandatory and sorted by utility_weight descending; Tier 2 (safe
        to wait) is opportunistic and sorted by utility-per-byte
        descending. next_window_start uses next_after(window.end_ts) --
        ContactPlan's public interface, per this module's own contract.
        If there IS no next window (this is the plan's last one), every
        remaining bundle is treated as at-risk by construction (float
        'inf' as next_window_start makes the at-risk test always true) --
        correct, since skipping now means NEVER_SCHEDULED, an equally
        permanent loss.
        """
        next_window = self.plan.next_after(window.end_ts)
        next_window_start = next_window.start_ts if next_window is not None else float("inf")

        at_risk: List[BundleRecord] = []
        safe: List[BundleRecord] = []
        for b in eligible:
            if b.expiration_ts < next_window_start:
                at_risk.append(b)
            else:
                safe.append(b)

        at_risk.sort(key=lambda b: CLASS_SPECS[b.traffic_class].utility_weight,
                     reverse=True)
        safe.sort(key=lambda b: CLASS_SPECS[b.traffic_class].utility_weight / b.size_bytes,
                  reverse=True)

        remaining_bits = window.raw_bit_budget()
        for tier_name, ordered in (("utility_at_risk", at_risk),
                                    ("utility_opportunistic", safe)):
            for b in ordered:
                bits_needed = b.size_bytes * 8
                if bits_needed <= remaining_bits:
                    b.transmission_ts = window.start_ts
                    b.contact_id = window.contact_id
                    b.mark(TerminalState.DELIVERED, ts=window.start_ts)
                    remaining_bits -= bits_needed
                    self._active_bytes -= b.size_bytes
                    self._log(window.start_ts, b, action="transmit",
                               reason=tier_name, contact_id=window.contact_id)
                else:
                    self._log(window.start_ts, b, action="defer",
                               reason="insufficient_budget",
                               contact_id=window.contact_id)

    # -- drain: UTILITY_PURE (S2-W6-7, bonus ablation) -----------------------
    def _drain_utility_pure(self, eligible: List[BundleRecord],
                             window: ContactWindow) -> None:
        """Ablation: Tier 2 logic (utility-per-byte density) ALONE, no
        at-risk gate. See SchedulingPolicy.UTILITY_PURE docstring."""
        ordered = sorted(
            eligible,
            key=lambda b: CLASS_SPECS[b.traffic_class].utility_weight / b.size_bytes,
            reverse=True,
        )
        self._drain_ordered(ordered, window, reason="utility_density_only")

    # -- drain: WFQ / WFQ_SKIP_OVER (Day 14) --------------------------------
    def _drain_wfq(self, eligible: List[BundleRecord], window: ContactWindow,
                   skip_over_deficit: bool) -> None:
        """EMERGENCY hard-preempts (Decision 1); TELEMETRY/SCIENCE_BULK/
        MEDIA fair-share the remainder via DRR (Shreedhar & Varghese
        1996), rotated each window to avoid positional bias.

        skip_over_deficit=False: standard DRR -- head-of-line non-fit
            ends that class's turn this round (SchedulingPolicy.WFQ).
        skip_over_deficit=True: experimental variant -- non-fit head is
            skipped, smaller bundles behind it in the same class's queue
            are still tried against the same unconsumed deficit
            (SchedulingPolicy.WFQ_SKIP_OVER). NOT standard DRR.
        """
        remaining_bits = window.raw_bit_budget()

        emergency = sorted(
            [b for b in eligible if b.traffic_class is TrafficClass.EMERGENCY],
            key=lambda b: b.ingress_ts,
        )
        for b in emergency:
            bits_needed = b.size_bytes * 8
            if bits_needed <= remaining_bits:
                b.transmission_ts = window.start_ts
                b.contact_id = window.contact_id
                b.mark(TerminalState.DELIVERED, ts=window.start_ts)
                remaining_bits -= bits_needed
                self._active_bytes -= b.size_bytes
                self._log(window.start_ts, b, action="transmit",
                           reason="rank0", contact_id=window.contact_id)
            else:
                self._log(window.start_ts, b, action="defer",
                           reason="insufficient_budget",
                           contact_id=window.contact_id)

        total_weight = sum(CLASS_SPECS[tc].queue_budget for tc in self._WFQ_CLASSES)
        quanta = {
            tc: remaining_bits * (CLASS_SPECS[tc].queue_budget / total_weight)
            for tc in self._WFQ_CLASSES
        }

        by_class: Dict[TrafficClass, List[BundleRecord]] = {
            tc: sorted([b for b in eligible if b.traffic_class is tc],
                       key=lambda b: b.ingress_ts)
            for tc in self._WFQ_CLASSES
        }

        n = len(self._WFQ_CLASSES)
        idx = self._rotation_index % n
        order = self._WFQ_CLASSES[idx:] + self._WFQ_CLASSES[:idx]

        for tc in order:
            queue = by_class[tc]
            if not queue:
                self._deficit[tc] = 0.0
                continue
            self._deficit[tc] += quanta[tc]

            if not skip_over_deficit:
                while queue:
                    head = queue[0]
                    bits_needed = head.size_bytes * 8
                    if bits_needed <= self._deficit[tc] and bits_needed <= remaining_bits:
                        queue.pop(0)
                        self._deficit[tc] -= bits_needed
                        remaining_bits -= bits_needed
                        head.transmission_ts = window.start_ts
                        head.contact_id = window.contact_id
                        head.mark(TerminalState.DELIVERED, ts=window.start_ts)
                        self._active_bytes -= head.size_bytes
                        self._log(window.start_ts, head, action="transmit",
                                   reason=f"wfq_{tc.value}",
                                   contact_id=window.contact_id)
                    else:
                        reason = ("insufficient_deficit"
                                   if bits_needed > self._deficit[tc]
                                   else "insufficient_budget")
                        self._log(window.start_ts, head, action="defer",
                                   reason=reason, contact_id=window.contact_id)
                        break
            else:
                still_queued = []
                for b in queue:
                    bits_needed = b.size_bytes * 8
                    if bits_needed <= self._deficit[tc] and bits_needed <= remaining_bits:
                        self._deficit[tc] -= bits_needed
                        remaining_bits -= bits_needed
                        b.transmission_ts = window.start_ts
                        b.contact_id = window.contact_id
                        b.mark(TerminalState.DELIVERED, ts=window.start_ts)
                        self._active_bytes -= b.size_bytes
                        self._log(window.start_ts, b, action="transmit",
                                   reason=f"wfq_skip_{tc.value}",
                                   contact_id=window.contact_id)
                    else:
                        still_queued.append(b)
                        reason = ("insufficient_deficit"
                                   if bits_needed > self._deficit[tc]
                                   else "insufficient_budget")
                        self._log(window.start_ts, b, action="defer",
                                   reason=reason, contact_id=window.contact_id)
                by_class[tc] = still_queued

        self._rotation_index = (self._rotation_index + 1) % n

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

        if last_end_ts is not None:
            self._admit_arrivals(pending_arrivals, float("inf"))
            self._expire_in_blackout(last_end_ts)
        else:
            self._admit_arrivals(pending_arrivals, float("inf"))

        for b in self._active:
            if b.terminal_state is TerminalState.PENDING:
                b.mark(TerminalState.NEVER_SCHEDULED)
                self._log(last_end_ts if last_end_ts is not None else 0.0,
                           b, action="drop", reason="never_scheduled")
        self._active = []
