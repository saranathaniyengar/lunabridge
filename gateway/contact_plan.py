"""
gateway/contact_plan.py

Source-agnostic contact-window model for LunaBridge's future DTN scheduler.
Answers exactly one question for a scheduler that does not yet exist: "is
the relay link up at timestamp ts, and if so, at what rate, and when does
it close?" Fed by synthetic windows today; ephemeris-derived windows land
Day 15 as a new generator function, with no change to ContactWindow or
ContactPlan themselves.

Out of scope, deliberately: scheduler/queue/admission logic, any
BundleRecord/TelemetryWriter/telemetry.py symbol, file I/O or JSON
persistence, ephemeris/orbit code.
"""

from bisect import bisect_right
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class ContactWindow:
    """
    One contact opportunity: the relay link is available from start_ts
    (inclusive) up to end_ts (exclusive), at a nominal link_rate_bps.

    Boundary convention -- [start_ts, end_ts): chosen so that two windows
    can sit back-to-back (window A's end_ts == window B's start_ts)
    without overlapping and without leaving a boundary instant unclaimed
    by either. At that exact timestamp, the STARTING window owns it, the
    ENDING window does not. Matches Python's own range/slice convention.

    link_rate_bps == 0 is rejected at construction, not accepted as a
    degraded contact. Zero capacity means it isn't a contact at all --
    admitting it would let a future scheduler "schedule" a bundle into a
    window that can never move a bit.
    """

    contact_id: str
    start_ts: float
    end_ts: float
    link_rate_bps: float
    relay_id: str = "relay-0"

    def __post_init__(self) -> None:
        if self.end_ts <= self.start_ts:
            raise ValueError(
                f"ContactWindow {self.contact_id!r}: end_ts ({self.end_ts}) "
                f"must be > start_ts ({self.start_ts}); zero-length and "
                f"inverted windows are not valid contacts."
            )
        if self.link_rate_bps <= 0:
            raise ValueError(
                f"ContactWindow {self.contact_id!r}: link_rate_bps "
                f"({self.link_rate_bps}) must be > 0; a zero-rate window "
                f"carries no capacity and is not a contact."
            )

    def covers(self, ts: float) -> bool:
        """True if ts falls inside this window under [start_ts, end_ts)."""
        return self.start_ts <= ts < self.end_ts

    @property
    def duration_s(self) -> float:
        return self.end_ts - self.start_ts

    def raw_bit_budget(self) -> float:
        """
        duration_s * link_rate_bps -- the RAW nominal bit count this
        window could carry at its nominal PHY rate, with NO derating.

        THIS IS NOT C_eff and is NOT the paper's "contact capacity per
        window" (SESSION_STATE.md Sec 10, items 14-15). C_eff = B *
        eta_spectral * eta_coding * eta_protocol * eta_contact, and
        contact capacity is C_eff * duration -- not this. This method
        uses the untouched nominal link_rate_bps. Do not substitute this
        value for the paper's contact-capacity figure; they look similar
        and are not the same number.
        """
        return self.duration_s * self.link_rate_bps


class ContactPlan:
    """
    An ordered, non-overlapping sequence of ContactWindows -- the relay's
    full schedule, source-agnostic (synthetic today, ephemeris-derived
    from Day 15 onward, same shape either way).

    Sorting: the constructor sorts the given windows by start_ts itself,
    rather than requiring pre-sorted input. Callers -- synthetic
    generators today, an ephemeris generator later -- shouldn't have to
    guarantee ordering; the class does it once, here, so every consumer
    downstream (active_at, next_after, the future scheduler) can rely on
    the windows always being sorted.

    Non-overlap: enforced at construction, checking only ADJACENT windows
    in sorted order. This is sufficient, not just convenient -- if any
    two windows overlapped, the sort would have to place some window
    between their start_ts values, and that window would itself overlap
    at least one of the pair. So an overlap always shows up between some
    adjacent pair post-sort; checking non-adjacent pairs adds nothing.
    Back-to-back windows (A.end_ts == B.start_ts) are NOT an overlap,
    consistent with ContactWindow's [start_ts, end_ts) convention.
    """

    def __init__(self, windows: Sequence[ContactWindow]):
        self._windows = sorted(windows, key=lambda w: w.start_ts)
        for prev, nxt in zip(self._windows, self._windows[1:]):
            if nxt.start_ts < prev.end_ts:
                raise ValueError(
                    f"ContactPlan: overlapping windows "
                    f"{prev.contact_id!r} [{prev.start_ts}, {prev.end_ts}) "
                    f"and {nxt.contact_id!r} [{nxt.start_ts}, {nxt.end_ts})."
                )
        self._starts = [w.start_ts for w in self._windows]

    def __len__(self) -> int:
        return len(self._windows)

    def active_at(self, ts: float) -> Optional[ContactWindow]:
        """The window covering ts under [start_ts, end_ts), or None."""
        idx = bisect_right(self._starts, ts) - 1
        if idx < 0:
            return None
        candidate = self._windows[idx]
        return candidate if candidate.covers(ts) else None

    def next_after(self, ts: float) -> Optional[ContactWindow]:
        """
        The window covering ts right now, if any; otherwise the earliest
        upcoming window (smallest start_ts > ts); otherwise None if
        nothing comes after ts. This is what lets the scheduler compute,
        for a bundle stuck in a blackout, how long until the link
        reopens -- and correctly returns "now" rather than "later" if
        ts already falls inside a window.
        """
        active = self.active_at(ts)
        if active is not None:
            return active
        idx = bisect_right(self._starts, ts)
        return self._windows[idx] if idx < len(self._windows) else None


def periodic_contact_plan(
    num_windows: int,
    period_s: float,
    duration_s: float,
    link_rate_bps: float,
    start_ts: float = 0.0,
    id_prefix: str = "periodic",
) -> ContactPlan:
    """
    N evenly-spaced synthetic windows: up for duration_s every period_s,
    starting at start_ts. Deliberately controllable, NOT random -- every
    parameter is explicit so results are reproducible test data, not an
    orbit or any claimed real-world lineage.

    Requires duration_s <= period_s (a window can't be longer than its
    own repeat interval without overlapping the next one -- ContactPlan
    would reject that anyway, but failing here with a clearer message is
    kinder to the caller).
    """
    if duration_s > period_s:
        raise ValueError(
            f"periodic_contact_plan: duration_s ({duration_s}) must be <= "
            f"period_s ({period_s}), or windows would overlap."
        )
    windows = [
        ContactWindow(
            contact_id=f"{id_prefix}-{i}",
            start_ts=start_ts + i * period_s,
            end_ts=start_ts + i * period_s + duration_s,
            link_rate_bps=link_rate_bps,
        )
        for i in range(num_windows)
    ]
    return ContactPlan(windows)


def fixture_single_short_window() -> ContactPlan:
    """
    Exactly one short window. FOR: a bundle arriving close to window
    close -- once the scheduler exists, this exercises the boundary
    between "just made it" and NEVER_SCHEDULED / TTL_EXPIRED. Kept tiny
    (10s) so "close to close" is trivial to construct in a scheduler
    test without inventing new arithmetic.
    """
    return ContactPlan([
        ContactWindow(contact_id="short-0", start_ts=0.0, end_ts=10.0, link_rate_bps=1000.0),
    ])


def fixture_back_to_back_windows() -> ContactPlan:
    """
    Two windows with zero gap between them (A.end_ts == B.start_ts). FOR:
    exercising the [start_ts, end_ts) boundary convention continuously --
    the instant 100.0 must belong to B, not A, and not both, and not
    neither. Any off-by-one in active_at/next_after shows up here first.
    """
    return ContactPlan([
        ContactWindow(contact_id="btb-0", start_ts=0.0, end_ts=100.0, link_rate_bps=1000.0),
        ContactWindow(contact_id="btb-1", start_ts=100.0, end_ts=200.0, link_rate_bps=1000.0),
    ])


def fixture_long_blackout_exceeds_max_ttl() -> ContactPlan:
    """
    One window, then a gap longer than the longest class TTL in
    gateway/traffic.py's CLASS_SPECS (SCIENCE_BULK, default_ttl_s =
    7*86400 = 604800.0 -- read directly from traffic.py's source on Day
    10; this file does not import traffic.py, the number is hardcoded
    here so the model stays dependency-free and source-agnostic). FOR:
    proving TTL expiry during a blackout independent of scheduling
    policy -- a bundle queued right before this gap opens must expire
    before the next window, regardless of what the scheduler does.

    Gap is 700000.0s (~8.1 days), comfortable margin over 604800.0s. If
    CLASS_SPECS is later revised to push a TTL past ~700000.0s, this
    fixture needs revisiting -- that's a large jump from today's max,
    but it's not impossible given several classes are still marked
    PLACEHOLDER pending the scheduler build.
    """
    return ContactPlan([
        ContactWindow(contact_id="blackout-pre", start_ts=0.0, end_ts=100.0, link_rate_bps=1000.0),
        ContactWindow(contact_id="blackout-post", start_ts=700100.0, end_ts=700200.0, link_rate_bps=1000.0),
    ])
