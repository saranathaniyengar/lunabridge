"""
Regression test for the offered-load calibration of the workload generator.

Guards the fix that made MEDIA a continuous CBR video source so the aggregate
offered load matches Paper 4's traffic model (~9-13 Mbps) instead of the old
~13.9 kbps. That old baseline forced R>1 only by throttling the backhaul to
10 kbps; with realistic MEDIA, R>1 emerges naturally against the real ~8 Mbps
effective backhaul (10 Mbps link x eta_contact~0.83).
"""

import numpy as np

from gateway.traffic import TrafficClass
from gateway.workload_generator import (
    MEDIA_BITRATE_BPS,
    MEDIA_STREAMS,
    expected_offered_load_mbps,
    generate_workload,
)

# Effective backhaul on the real 1-SV plan: 10 Mbps x eta_contact (~0.834).
C_BACKHAUL_EFF_MBPS = 10.0 * 0.834


def test_baseline_offered_load_matches_paper():
    """Baseline offered load lands in the paper's 9-13 Mbps demand band."""
    load = expected_offered_load_mbps(rate_multiplier=1.0)
    assert 9.0 <= load <= 13.0, f"offered load {load:.2f} Mbps outside 9-13"


def test_R_exceeds_one_naturally():
    """Baseline demand exceeds the effective backhaul, so R>1 without any
    stress/throttling of the link."""
    load = expected_offered_load_mbps(rate_multiplier=1.0)
    R = load / C_BACKHAUL_EFF_MBPS
    assert R > 1.0, f"R={R:.2f} <= 1; realistic traffic should overload backhaul"


def test_media_dominates_offered_load():
    """MEDIA (continuous video) is the dominant source; the bursty classes are
    negligible -- this is the whole reason the old 13.9 kbps model was wrong."""
    total = expected_offered_load_mbps(1.0)
    media = MEDIA_STREAMS * MEDIA_BITRATE_BPS / 1e6
    assert media / total > 0.95


def test_one_feed_is_about_six_mbps():
    """One 1080p feed ~= 6 Mbps (the R-just-below-1 case in the paper)."""
    per_feed = MEDIA_BITRATE_BPS / 1e6
    assert 4.0 <= per_feed <= 8.0


def test_generated_stream_matches_analytic():
    """A generated stream (short span, for speed) reproduces the analytic mean
    offered load within a few percent."""
    span = 4 * 3600.0  # 4 h is enough for the CBR term to converge
    recs = generate_workload(seed=7, span_seconds=span)
    measured = sum(r.size_bytes for r in recs) * 8.0 / span / 1e6
    analytic = expected_offered_load_mbps(1.0)
    assert abs(measured - analytic) / analytic < 0.10

    # MEDIA should be the dominant class by delivered bytes.
    media_bytes = sum(r.size_bytes for r in recs
                      if r.traffic_class is TrafficClass.MEDIA)
    assert media_bytes / sum(r.size_bytes for r in recs) > 0.90


def test_rate_multiplier_scales_load_linearly():
    assert np.isclose(expected_offered_load_mbps(2.0),
                      2.0 * expected_offered_load_mbps(1.0), rtol=1e-6)
