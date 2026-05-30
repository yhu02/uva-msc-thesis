"""Per-pod utilization-vs-request derivation.

Joins per-pod CPU/memory time-series from the Prometheus prober with the
``resourceSpecs`` requests captured by ``_collect_pod_status``.  The
output lets downstream analysis distinguish "pod was throttled because
it hit its own limit" from "pod was throttled because the node was hot"
(H7 attribution).
"""

import math
from typing import Any, Dict, List, Optional


def parse_cpu_quantity(quantity: Optional[str]) -> Optional[float]:
    """Convert a K8s CPU quantity (e.g. ``"100m"``, ``"1.5"``) to cores."""
    if quantity is None:
        return None
    s = str(quantity).strip()
    if not s:
        return None
    try:
        if s.endswith("m"):
            return float(s[:-1]) / 1000.0
        return float(s)
    except (ValueError, AttributeError):
        return None


# Sorted longest-suffix-first so "Mi" is matched before "M".
_MEMORY_SUFFIXES = [
    ("Ki", 1024),
    ("Mi", 1024**2),
    ("Gi", 1024**3),
    ("Ti", 1024**4),
    ("Pi", 1024**5),
    ("Ei", 1024**6),
    ("k", 1000),
    ("K", 1000),
    ("M", 1000**2),
    ("G", 1000**3),
    ("T", 1000**4),
    ("P", 1000**5),
    ("E", 1000**6),
]


def parse_memory_quantity(quantity: Optional[str]) -> Optional[int]:
    """Convert a K8s memory quantity (e.g. ``"256Mi"``, ``"1Gi"``) to bytes."""
    if quantity is None:
        return None
    s = str(quantity).strip()
    if not s:
        return None
    for suffix, mult in _MEMORY_SUFFIXES:
        if s.endswith(suffix):
            try:
                return int(float(s[: -len(suffix)]) * mult)
            except ValueError:
                return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _sum_requests(pod: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Sum CPU and memory requests across all containers in a pod."""
    cpu_sum = 0.0
    mem_sum = 0
    any_cpu = False
    any_mem = False
    for spec in pod.get("resourceSpecs", []) or []:
        req = spec.get("requests") or {}
        cpu = parse_cpu_quantity(req.get("cpu"))
        if cpu is not None:
            cpu_sum += cpu
            any_cpu = True
        mem = parse_memory_quantity(req.get("memory"))
        if mem is not None:
            mem_sum += mem
            any_mem = True
    return {
        "cpuRequestCores": cpu_sum if any_cpu else None,
        "memoryRequestBytes": mem_sum if any_mem else None,
    }


def _walk_time_series(
    time_series: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Dict[str, List[float]]]]:
    """Group per-pod CPU/memory samples by ``phase -> pod -> label``."""
    by_phase: Dict[str, Dict[str, Dict[str, List[float]]]] = {}
    for entry in time_series:
        phase = entry.get("phase")
        if not phase:
            continue
        for label in ("cpu_usage", "memory_usage"):
            for item in entry.get("metrics", {}).get(label, []) or []:
                pod_name = item.get("metric", {}).get("pod")
                if not pod_name:
                    continue
                value = item.get("value")
                if not value or len(value) < 2:
                    continue
                try:
                    val = float(value[1])
                except (TypeError, ValueError):
                    continue
                # Prometheus emits "NaN"/"+Inf"/"-Inf" for no-data; float()
                # parses those without raising.  Drop them — a non-finite
                # mean later reaches int(mean_mem), which raises ValueError,
                # and round(nan) would emit invalid JSON.  Mirrors the
                # summary-path guard in prometheus.py.
                if not math.isfinite(val):
                    continue
                by_phase.setdefault(phase, {}).setdefault(pod_name, {}).setdefault(
                    label, []
                ).append(val)
    return by_phase


def compute_per_pod_utilization(
    pod_status: Optional[Dict[str, Any]],
    prometheus_data: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute per-pod, per-phase CPU/memory utilization fractions.

    Returns ``{"pods": {pod_name: {cpuRequestCores, memoryRequestBytes,
    phases: {phase: {cpuUsageCores, cpuFraction, memoryUsageBytes,
    memoryFraction}}}}}``.  Pods without requests, or phases without
    Prometheus samples, are reported with only the keys that have data —
    callers should treat missing keys as "not measured."
    """
    if not pod_status or not prometheus_data or not prometheus_data.get("available"):
        return {"pods": {}}

    pod_requests = {
        pod["name"]: _sum_requests(pod) for pod in pod_status.get("pods", []) if pod.get("name")
    }
    phase_samples = _walk_time_series(prometheus_data.get("timeSeries", []))

    out_pods: Dict[str, Any] = {}
    for pod_name, reqs in pod_requests.items():
        pod_out: Dict[str, Any] = {
            "cpuRequestCores": (
                round(reqs["cpuRequestCores"], 4) if reqs["cpuRequestCores"] is not None else None
            ),
            "memoryRequestBytes": reqs["memoryRequestBytes"],
            "phases": {},
        }
        for phase, by_pod in phase_samples.items():
            pod_samples = by_pod.get(pod_name, {})
            phase_entry: Dict[str, Any] = {}
            cpu_vals = pod_samples.get("cpu_usage", [])
            mem_vals = pod_samples.get("memory_usage", [])
            if cpu_vals:
                mean_cpu = sum(cpu_vals) / len(cpu_vals)
                phase_entry["cpuUsageCores"] = round(mean_cpu, 4)
                cpu_req = reqs["cpuRequestCores"]
                if cpu_req and cpu_req > 0:
                    phase_entry["cpuFraction"] = round(mean_cpu / cpu_req, 4)
            if mem_vals:
                mean_mem = sum(mem_vals) / len(mem_vals)
                phase_entry["memoryUsageBytes"] = int(mean_mem)
                mem_req = reqs["memoryRequestBytes"]
                if mem_req and mem_req > 0:
                    phase_entry["memoryFraction"] = round(mean_mem / mem_req, 4)
            if phase_entry:
                pod_out["phases"][phase] = phase_entry
        out_pods[pod_name] = pod_out

    return {"pods": out_pods}
