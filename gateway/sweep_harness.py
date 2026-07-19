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
    """Same real window timing as the real plan, link_rate_bps replaced.
    NOT a claimed real spec -- forces genuine bandwidth scarcity so the 7
    policies' real differences show up (real 10 Mbps rate makes every
    window's budget vastly exceed any realistic backlog -- confirmed R~890
    would be needed to matter, computationally intractable)."""
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
              csv_path: str, seed: int = 42) -> Dict[float, Dict[str, dict]]:
    plan = load_real_contact_plan(csv_path)
    results = {}
    for ttl in ttl_values:
        bundles = generate_workload(seed=seed)
        for b in bundles:
            if b.traffic_class is traffic_class:
                b.set_ttl(ttl_s=ttl)
        results[ttl] = run_all_policies(bundles, plan)
    return results
