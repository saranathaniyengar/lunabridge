"""
gateway/sweep_harness.py -- shared sweep infrastructure for Fig 3/Fig 8.
"""
import csv
from typing import Dict, List

from gateway.contact_plan import ContactPlan, ContactWindow
from gateway.scheduler import Scheduler, SchedulingPolicy
from gateway.telemetry import (
    BundleRecord, delivery_ratio_by_class, mission_utility, starvation_summary,
)
from gateway.traffic import TrafficClass
from gateway.workload_generator import generate_workload

ALL_POLICIES = list(SchedulingPolicy)
MAX_QUEUE_BYTES = 20_000_000_000  # 20 GB -- generous, isolates bandwidth effects from admission overflow


def load_real_contact_plan(csv_path: str) -> ContactPlan:
    windows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            windows.append(ContactWindow(
                contact_id=row["start_utc"],
                start_ts=float(row["start_sec"]),
                end_ts=float(row["end_sec"]),
                link_rate_bps=float(row["rate_bps"]),
            ))
    return ContactPlan(windows)


def load_stress_contact_plan(csv_path: str, rate_bps_override: float) -> ContactPlan:
    windows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            windows.append(ContactWindow(
                contact_id=row["start_utc"],
                start_ts=float(row["start_sec"]),
                end_ts=float(row["end_sec"]),
                link_rate_bps=rate_bps_override,
            ))
    return ContactPlan(windows)


def run_all_policies(bundles: List[BundleRecord], plan: ContactPlan) -> Dict[str, dict]:
    results = {}
    for policy in ALL_POLICIES:
        run_bundles = [
            BundleRecord(
                bundle_id=b.bundle_id, flow_id=b.flow_id,
                traffic_class=b.traffic_class, size_bytes=b.size_bytes,
                ingress_ts=b.ingress_ts,
            ) for b in bundles
        ]
        for rb, orig in zip(run_bundles, bundles):
            rb.set_ttl(ttl_s=orig.expiration_ts - orig.ingress_ts)

        sched = Scheduler(plan, max_queue_bytes=MAX_QUEUE_BYTES, policy=policy)
        sched.run(run_bundles)

        results[policy.value] = {
            "mission_utility": mission_utility(run_bundles),
            "delivery_ratio_by_class": delivery_ratio_by_class(run_bundles),
            "starvation_summary": starvation_summary(run_bundles),
        }
    return results


def sweep_R(r_values: List[float], csv_path: str, seed: int = 42,
            stress: bool = False, stress_rate_bps: float = 10_000.0) -> Dict[float, Dict[str, dict]]:
    plan = (load_stress_contact_plan(csv_path, stress_rate_bps) if stress
            else load_real_contact_plan(csv_path))
    results = {}
    for r in r_values:
        bundles = generate_workload(seed=seed, rate_multiplier=r)
        results[r] = run_all_policies(bundles, plan)
    return results


def sweep_ttl(traffic_class: TrafficClass, ttl_values: List[float],
              csv_path: str, seed: int = 42, rate_multiplier: float = 1.0,
              stress: bool = False, stress_rate_bps: float = 10_000.0) -> Dict[float, Dict[str, dict]]:
    """Fig 8: for each candidate TTL, override ONLY traffic_class's TTL on
    the generated bundles -- every other class keeps its locked TTL.
    rate_multiplier lets this run at a fixed congestion level (e.g. R=20,
    the clean/understood zone from Fig 3) rather than only R=1."""
    plan = (load_stress_contact_plan(csv_path, stress_rate_bps) if stress
            else load_real_contact_plan(csv_path))
    results = {}
    for ttl in ttl_values:
        bundles = generate_workload(seed=seed, rate_multiplier=rate_multiplier)
        for b in bundles:
            if b.traffic_class is traffic_class:
                b.set_ttl(ttl_s=ttl)
        results[ttl] = run_all_policies(bundles, plan)
    return results


def compute_triage_breakdown(bundles: List[BundleRecord], plan: ContactPlan) -> Dict[str, Dict[str, Dict[str, int]]]:
    """Fig 4: for each policy, run once and return the FULL per-class x
    per-terminal-state count matrix -- breakdown[policy][class][state] = count.

    Unlike delivery_ratio_by_class/starvation_summary (which collapse
    failure modes into one ratio or partial per-class dicts), this keeps
    all four TerminalStates (DELIVERED/TTL_EXPIRED/QUEUE_OVERFLOW/
    NEVER_SCHEDULED) broken out per class -- the distinction
    TerminalState's own docstring calls "the whole point of this enum"."""
    breakdown = {}
    for policy in ALL_POLICIES:
        run_bundles = [
            BundleRecord(
                bundle_id=b.bundle_id, flow_id=b.flow_id,
                traffic_class=b.traffic_class, size_bytes=b.size_bytes,
                ingress_ts=b.ingress_ts,
            ) for b in bundles
        ]
        for rb, orig in zip(run_bundles, bundles):
            rb.set_ttl(ttl_s=orig.expiration_ts - orig.ingress_ts)

        sched = Scheduler(plan, max_queue_bytes=MAX_QUEUE_BYTES, policy=policy)
        sched.run(run_bundles)

        counts: Dict[str, Dict[str, int]] = {}
        for b in run_bundles:
            cls = b.traffic_class.value
            state = b.terminal_state.value
            counts.setdefault(cls, {}).setdefault(state, 0)
            counts[cls][state] += 1
        breakdown[policy.value] = counts
    return breakdown
