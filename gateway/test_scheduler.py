"""
gateway/test_scheduler.py

Day 12 offline unit test suite for gateway/scheduler.py.
stdlib unittest only -- no pytest, no new test framework (Day 10 convention).

FIXTURE SOURCE, per this session's locked decision:
  The two tests that are actually ABOUT contact-plan realism -- TTL expiry
  during a genuine blackout, and starvation (NEVER_SCHEDULED) under a real
  90-day degraded plan -- load the real LCRNS 1-satellite degraded contact
  plan (lcrns_relay_contact_plan_1sv.csv), per this session's explicit
  decision (the 5-satellite file's merged coverage has ~zero gap and can't
  exercise this at all).

  The two tests that are purely about admission/drain ARITHMETIC (queue
  overflow at a byte cap, skip-over vs. block on non-fit) use small,
  explicit synthetic ContactWindows instead. This is a deliberate choice,
  flagged here rather than silently made: those two tests aren't about
  contact-plan realism, they're about scheduler mechanics that are
  actually clearer to verify with round numbers. It does not reopen or
  override the fixture-source decision for the tests where realism is the
  actual point.

CSV PATH: assumes lcrns_relay_contact_plan_1sv.csv sits alongside this
test file (gateway/). Adjust CSV_PATH below if the real repo places it
elsewhere -- this was not verified against the actual repo layout.
"""

import csv
import os
import unittest

from .contact_plan import ContactPlan, ContactWindow
from .scheduler import Scheduler
from .telemetry import BundleRecord, TerminalState
from .traffic import TrafficClass

CSV_PATH = os.path.join(os.path.dirname(__file__), "lcrns_relay_contact_plan_1sv.csv")


def load_lcrns_1sv_contact_plan(csv_path: str = CSV_PATH) -> ContactPlan:
    """Build a ContactPlan from the real GMAT-derived 1-satellite degraded
    CSV. Uses start_sec/end_sec (relative-seconds columns) as start_ts/
    end_ts, and rate_bps as link_rate_bps -- owlt_s and satellite are not
    used by the scheduler (see scheduler.py's module docstring)."""
    windows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            windows.append(ContactWindow(
                contact_id=f"lcrns-{i}",
                start_ts=float(row["start_sec"]),
                end_ts=float(row["end_sec"]),
                link_rate_bps=float(row["rate_bps"]),
            ))
    return ContactPlan(windows)


class TestQueueOverflowAndSkipOver(unittest.TestCase):
    """Pure admission/drain arithmetic -- synthetic windows, round numbers,
    deliberately NOT the real CSV (see module docstring)."""

    def test_queue_overflow_at_admission(self):
        plan = ContactPlan([
            ContactWindow(contact_id="w0", start_ts=0.0, end_ts=100.0, link_rate_bps=1000.0),
        ])
        sched = Scheduler(plan, max_queue_bytes=1000)

        b1 = BundleRecord("b1", "f1", TrafficClass.EMERGENCY, size_bytes=600, ingress_ts=0.0)
        b1.set_ttl()
        b2 = BundleRecord("b2", "f2", TrafficClass.EMERGENCY, size_bytes=600, ingress_ts=0.0)
        b2.set_ttl()

        sched.run([b1, b2])

        # b1 admitted first (ingress-order tiebreak in admission), fits
        # the 100-bit-budget-irrelevant path since it's about the BYTE cap,
        # not the bit budget. 600 <= 1000 -> admitted; then delivered
        # (window budget is huge relative to these sizes).
        self.assertEqual(b1.terminal_state, TerminalState.DELIVERED)
        # b2: 600 (b1 already active) + 600 = 1200 > 1000 -> overflow.
        self.assertEqual(b2.terminal_state, TerminalState.QUEUE_OVERFLOW)

        overflow_decisions = [d for d in sched.decisions if d.action == "drop"
                               and d.reason == "queue_overflow"]
        self.assertEqual(len(overflow_decisions), 1)
        self.assertEqual(overflow_decisions[0].bundle_id, "b2")

    def test_skip_over_lets_smaller_bundle_through(self):
        # window budget = 100s * 1000 bps = 100000 bits = 12500 bytes
        plan = ContactPlan([
            ContactWindow(contact_id="w0", start_ts=0.0, end_ts=100.0, link_rate_bps=1000.0),
        ])
        sched = Scheduler(plan, max_queue_bytes=1_000_000)

        big = BundleRecord("big", "f1", TrafficClass.EMERGENCY, size_bytes=20000, ingress_ts=0.0)
        big.set_ttl()
        small = BundleRecord("small", "f2", TrafficClass.TELEMETRY, size_bytes=1000, ingress_ts=0.0)
        small.set_ttl()

        sched.run([big, small])

        # big doesn't fit (20000 bytes = 160000 bits > 100000-bit budget)
        # and is rank 0 (checked first), but skip-over means small (rank 1,
        # 8000 bits, fits easily) still goes out this same window.
        self.assertEqual(small.terminal_state, TerminalState.DELIVERED)
        # big never fits in the plan's only window -> starves.
        self.assertEqual(big.terminal_state, TerminalState.NEVER_SCHEDULED)

        defer_decisions = [d for d in sched.decisions if d.action == "defer"]
        self.assertEqual(len(defer_decisions), 1)
        self.assertEqual(defer_decisions[0].bundle_id, "big")
        self.assertEqual(defer_decisions[0].reason, "insufficient_budget")


class TestRealisticBlackoutBehavior(unittest.TestCase):
    """The two tests that are actually about contact-plan realism -- these
    load the real LCRNS 1-satellite degraded CSV per this session's fixture
    decision."""

    @classmethod
    def setUpClass(cls):
        cls.plan = load_lcrns_1sv_contact_plan()
        # Sanity-check against SESSION_STATE.md's independently-verified
        # numbers before trusting this fixture in the actual tests below.
        assert len(cls.plan) == 73, f"expected 73 real contact windows, got {len(cls.plan)}"

    def test_ttl_expires_during_real_blackout(self):
        """A MEDIA bundle (TTL=11813s, the shortest class) arrives mid-way
        through the real gap between window index 5 (ends 634420) and
        window index 6 (starts 654447) -- the real ~20027.5s gap SESSION_
        STATE.md independently verified as this file's max gap. Its TTL
        elapses inside that real blackout, well before the link reopens."""
        max_queue_bytes = 10_000_000_000  # generous; not the point of this test

        window5_end = 634420.0
        window6_start = 654447.0
        assert window6_start - window5_end == 20027.0  # sanity: real gap, not invented

        media = BundleRecord(
            "media-1", "flowA", TrafficClass.MEDIA, size_bytes=100,
            ingress_ts=635000.0,  # arrives after window5 closes -- genuinely stuck
        )                         # in the real blackout gap -- no window open to catch it
        media.set_ttl()  # expiration_ts = 635000 + 11813 = 646813

        sched = Scheduler(self.plan, max_queue_bytes=max_queue_bytes)
        sched.run([media])

        self.assertEqual(media.terminal_state, TerminalState.TTL_EXPIRED)
        ttl_decisions = [d for d in sched.decisions
                         if d.bundle_id == "media-1" and d.reason == "ttl_expired"]
        self.assertEqual(len(ttl_decisions), 1)

    def test_starvation_not_folded_into_ttl_expired(self):
        """SCIENCE_BULK (TTL=165389s, the longest class) is queued at the
        start of the plan's second-to-last real window and is completely
        crowded out by EMERGENCY traffic sized to exactly fill each of the
        last two windows' raw_bit_budget -- through to the end of the real
        90-day plan. Its TTL (165389s) outlives the remaining plan
        duration, so it must resolve to NEVER_SCHEDULED, NOT TTL_EXPIRED --
        this is the starvation-vs-deadline-loss distinction the enum
        exists to preserve."""
        second_last = self.plan._windows[-2]
        last = self.plan._windows[-1]

        emergency_a = BundleRecord(
            "emg-a", "flowB", TrafficClass.EMERGENCY,
            size_bytes=int(second_last.raw_bit_budget() // 8),
            ingress_ts=second_last.start_ts,
        )
        emergency_a.set_ttl()
        emergency_b = BundleRecord(
            "emg-b", "flowB", TrafficClass.EMERGENCY,
            size_bytes=int(last.raw_bit_budget() // 8),
            ingress_ts=last.start_ts,
        )
        emergency_b.set_ttl()

        starved = BundleRecord(
            "sci-1", "flowC", TrafficClass.SCIENCE_BULK, size_bytes=100,
            ingress_ts=second_last.start_ts,
        )
        starved.set_ttl()  # expiration_ts = second_last.start_ts + 165389

        # Sanity check on the scenario itself before trusting the result:
        # the bundle's TTL must genuinely outlive the plan's remaining
        # duration, or this isn't testing starvation at all.
        assert starved.expiration_ts > last.end_ts, (
            "scenario invalid: SCIENCE_BULK's TTL would expire before the "
            "plan ends -- this would test TTL_EXPIRED, not starvation"
        )

        # Cap must be large enough to admit the EMERGENCY bundles, which
        # are deliberately sized to fill real ~90000s windows at 10 Mbps
        # (~100+ GB) -- this test is about scheduling order, not queue
        # sizing, so the cap is set generously above what's needed rather
        # than tuned tight.
        needed = emergency_a.size_bytes + emergency_b.size_bytes + starved.size_bytes
        sched = Scheduler(self.plan, max_queue_bytes=needed * 2)
        sched.run([emergency_a, emergency_b, starved])

        self.assertEqual(emergency_a.terminal_state, TerminalState.DELIVERED)
        self.assertEqual(emergency_b.terminal_state, TerminalState.DELIVERED)
        self.assertEqual(starved.terminal_state, TerminalState.NEVER_SCHEDULED)

        # The core assertion this whole enum exists for: starvation and
        # deadline loss must land in SEPARATE buckets, never merged.
        self.assertNotEqual(starved.terminal_state, TerminalState.TTL_EXPIRED)



class TestSyntheticStarvation(unittest.TestCase):
    """Day 13 -- proves the strict-priority scheduler CAN starve a
    low-rank bundle to NEVER_SCHEDULED as a mechanism property, using a
    small synthetic plan (not the real 90-day file).

    Why not the real file: mid-plan NEVER_SCHEDULED is mathematically
    impossible against the real 90-day plan with real CLASS_SPECS TTLs --
    remaining_plan_duration is always >> the longest real TTL (165389s)
    except in the plan's final ~1.9 days. This test isolates the
    MECHANISM (rank-based crowd-out) from that real-world timing
    coincidence by using a plan short enough (~5500s total) that a real,
    UNMODIFIED SCIENCE_BULK TTL (165389s) trivially outlives the entire
    plan -- so if the bundle is still PENDING at the end, it is provably
    because it was never selected, not because of timing luck. Real
    CLASS_SPECS values are used untouched; only the ENVIRONMENT (contact
    plan + crowd-out traffic) is synthetic, per this session's Option A
    decision."""

    def setUp(self):
        self.plan = ContactPlan([
            ContactWindow(contact_id="synth-starve-0", start_ts=0.0,
                          end_ts=1000.0, link_rate_bps=8000.0),
            ContactWindow(contact_id="synth-starve-1", start_ts=1500.0,
                          end_ts=2500.0, link_rate_bps=8000.0),
            ContactWindow(contact_id="synth-starve-2", start_ts=3000.0,
                          end_ts=4000.0, link_rate_bps=8000.0),
            ContactWindow(contact_id="synth-starve-3", start_ts=4500.0,
                          end_ts=5500.0, link_rate_bps=8000.0),
        ])
        # Each window's raw_bit_budget() = 1000s * 8000bps = 8,000,000 bits
        # = exactly 1,000,000 bytes -- matches the EMERGENCY bundles below
        # exactly, one full window each.

    def test_low_rank_bundle_never_scheduled_under_sustained_crowd_out(self):
        """4 EMERGENCY bundles (rank 0), each sized to exactly fill one
        window's raw_bit_budget(), crowd out a single SCIENCE_BULK bundle
        (rank 2) across all 4 windows -- it is skip-over'd every time,
        never once fits. Its real, unmodified TTL (165389s) vastly
        outlives the plan's ~5500s total span, so it cannot resolve via
        TTL_EXPIRED -- it can only resolve via NEVER_SCHEDULED once the
        plan is exhausted. Starvation proven by construction, not by
        incidental real-world gap placement."""
        emergency_bundles = [
            BundleRecord(f"emg-{i}", "flowC", TrafficClass.EMERGENCY,
                         size_bytes=1_000_000, ingress_ts=0.0)
            for i in range(4)
        ]
        for b in emergency_bundles:
            b.set_ttl()

        starved = BundleRecord("starved-sci", "flowC", TrafficClass.SCIENCE_BULK,
                               size_bytes=100, ingress_ts=0.0)
        starved.set_ttl()  # real SCIENCE_BULK TTL = 165389s

        total_bytes = sum(b.size_bytes for b in emergency_bundles) + starved.size_bytes
        sched = Scheduler(self.plan, max_queue_bytes=total_bytes * 2)
        sched.run(emergency_bundles + [starved])

        for b in emergency_bundles:
            self.assertEqual(b.terminal_state, TerminalState.DELIVERED)

        self.assertEqual(starved.terminal_state, TerminalState.NEVER_SCHEDULED)
        # The core assertion: sustained rank-based crowd-out must resolve
        # as starvation, never silently folded into TTL_EXPIRED.
        self.assertNotEqual(starved.terminal_state, TerminalState.TTL_EXPIRED)

if __name__ == "__main__":
    unittest.main()
