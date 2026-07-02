import unittest

from gateway.contact_plan import (
    ContactWindow,
    ContactPlan,
    periodic_contact_plan,
    fixture_single_short_window,
    fixture_back_to_back_windows,
    fixture_long_blackout_exceeds_max_ttl,
)


class TestContactWindowValidation(unittest.TestCase):
    def test_valid_construction(self):
        w = ContactWindow(contact_id="w1", start_ts=100.0, end_ts=200.0, link_rate_bps=1000.0)
        self.assertEqual(w.duration_s, 100.0)

    def test_rejects_zero_length_window(self):
        with self.assertRaises(ValueError):
            ContactWindow(contact_id="w1", start_ts=100.0, end_ts=100.0, link_rate_bps=1000.0)

    def test_rejects_inverted_window(self):
        with self.assertRaises(ValueError):
            ContactWindow(contact_id="w1", start_ts=200.0, end_ts=100.0, link_rate_bps=1000.0)

    def test_rejects_zero_rate(self):
        with self.assertRaises(ValueError):
            ContactWindow(contact_id="w1", start_ts=100.0, end_ts=200.0, link_rate_bps=0.0)

    def test_rejects_negative_rate(self):
        with self.assertRaises(ValueError):
            ContactWindow(contact_id="w1", start_ts=100.0, end_ts=200.0, link_rate_bps=-5.0)

    def test_covers_boundaries(self):
        w = ContactWindow(contact_id="w1", start_ts=100.0, end_ts=200.0, link_rate_bps=1000.0)
        self.assertTrue(w.covers(100.0))
        self.assertFalse(w.covers(200.0))
        self.assertTrue(w.covers(150.0))
        self.assertFalse(w.covers(99.9))
        self.assertFalse(w.covers(200.1))

    def test_raw_bit_budget(self):
        w = ContactWindow(contact_id="w1", start_ts=0.0, end_ts=10.0, link_rate_bps=100.0)
        self.assertEqual(w.raw_bit_budget(), 1000.0)

    def test_default_relay_id(self):
        w = ContactWindow(contact_id="w1", start_ts=0.0, end_ts=10.0, link_rate_bps=100.0)
        self.assertEqual(w.relay_id, "relay-0")


class TestContactPlanConstruction(unittest.TestCase):
    def test_rejects_overlapping_windows(self):
        a = ContactWindow(contact_id="a", start_ts=0.0, end_ts=100.0, link_rate_bps=100.0)
        b = ContactWindow(contact_id="b", start_ts=50.0, end_ts=150.0, link_rate_bps=100.0)
        with self.assertRaises(ValueError):
            ContactPlan([a, b])

    def test_allows_back_to_back_windows(self):
        a = ContactWindow(contact_id="a", start_ts=0.0, end_ts=100.0, link_rate_bps=100.0)
        b = ContactWindow(contact_id="b", start_ts=100.0, end_ts=200.0, link_rate_bps=100.0)
        plan = ContactPlan([a, b])
        self.assertEqual(len(plan), 2)

    def test_robust_to_out_of_order_input(self):
        a = ContactWindow(contact_id="a", start_ts=0.0, end_ts=100.0, link_rate_bps=100.0)
        b = ContactWindow(contact_id="b", start_ts=200.0, end_ts=300.0, link_rate_bps=100.0)
        plan_in_order = ContactPlan([a, b])
        plan_out_of_order = ContactPlan([b, a])
        self.assertEqual(plan_in_order.active_at(50.0), plan_out_of_order.active_at(50.0))
        self.assertEqual(plan_in_order.next_after(150.0), plan_out_of_order.next_after(150.0))


class TestContactPlanQueries(unittest.TestCase):
    def setUp(self):
        self.a = ContactWindow(contact_id="a", start_ts=100.0, end_ts=200.0, link_rate_bps=100.0)
        self.b = ContactWindow(contact_id="b", start_ts=300.0, end_ts=400.0, link_rate_bps=100.0)
        self.plan = ContactPlan([self.a, self.b])

    def test_active_at_inside_window(self):
        self.assertIs(self.plan.active_at(150.0), self.a)

    def test_active_at_in_gap(self):
        self.assertIsNone(self.plan.active_at(250.0))

    def test_active_at_before_first(self):
        self.assertIsNone(self.plan.active_at(50.0))

    def test_active_at_after_last(self):
        self.assertIsNone(self.plan.active_at(450.0))

    def test_active_at_start_boundary(self):
        self.assertIs(self.plan.active_at(100.0), self.a)

    def test_active_at_end_boundary(self):
        self.assertIsNone(self.plan.active_at(200.0))

    def test_next_after_inside_window(self):
        self.assertIs(self.plan.next_after(150.0), self.a)

    def test_next_after_in_gap(self):
        self.assertIs(self.plan.next_after(250.0), self.b)

    def test_next_after_before_first(self):
        self.assertIs(self.plan.next_after(50.0), self.a)

    def test_next_after_after_last(self):
        self.assertIsNone(self.plan.next_after(450.0))

    def test_next_after_start_boundary(self):
        self.assertIs(self.plan.next_after(100.0), self.a)

    def test_next_after_end_boundary(self):
        self.assertIs(self.plan.next_after(200.0), self.b)


class TestPeriodicGenerator(unittest.TestCase):
    def test_produces_requested_count_and_spacing(self):
        plan = periodic_contact_plan(num_windows=5, period_s=100.0, duration_s=20.0, link_rate_bps=500.0)
        self.assertEqual(len(plan), 5)
        for i in range(5):
            w = plan.active_at(i * 100.0)
            self.assertIsNotNone(w)
            self.assertEqual(w.start_ts, i * 100.0)
            self.assertEqual(w.duration_s, 20.0)

    def test_rejects_duration_longer_than_period(self):
        with self.assertRaises(ValueError):
            periodic_contact_plan(num_windows=3, period_s=50.0, duration_s=60.0, link_rate_bps=500.0)


class TestNamedFixtures(unittest.TestCase):
    def test_single_short_window_shape(self):
        plan = fixture_single_short_window()
        self.assertEqual(len(plan), 1)
        w = plan.active_at(5.0)
        self.assertIsNotNone(w)
        self.assertEqual(w.duration_s, 10.0)

    def test_back_to_back_boundary_is_distinguishable(self):
        plan = fixture_back_to_back_windows()
        self.assertEqual(len(plan), 2)
        first = plan.active_at(99.999)
        second = plan.active_at(100.0)
        self.assertEqual(first.contact_id, "btb-0")
        self.assertEqual(second.contact_id, "btb-1")
        self.assertNotEqual(first.contact_id, second.contact_id)
        # next_after must agree with active_at inside both windows -- a
        # mismatch here is exactly the kind of bug that hides until the
        # scheduler is built on top of it.
        self.assertIs(plan.next_after(99.999), first)
        self.assertIs(plan.next_after(100.0), second)

    def test_long_blackout_exceeds_max_class_ttl(self):
        plan = fixture_long_blackout_exceeds_max_ttl()
        pre = plan.active_at(50.0)
        post = plan.next_after(150.0)  # 150.0 sits in the gap
        gap = post.start_ts - pre.end_ts
        MAX_KNOWN_TTL_S = 604800.0  # SCIENCE_BULK, gateway/traffic.py CLASS_SPECS
        self.assertGreater(gap, MAX_KNOWN_TTL_S)
        self.assertIsNone(plan.active_at(pre.end_ts + gap / 2))
        self.assertIs(plan.next_after(pre.end_ts + gap / 2), post)


if __name__ == "__main__":
    unittest.main()
