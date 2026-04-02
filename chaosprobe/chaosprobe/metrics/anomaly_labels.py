"""Anomaly label generation for ML training.

Produces structured ``anomalyLabels`` from chaos experiment metadata,
providing ground-truth labels that pair fault injection events with
timestamps, targets, affected services, and severity levels.

These labels enable supervised learning for anomaly classification:
each label says "anomaly type X happened at time T affecting service S."
"""

from typing import Any, Dict, List, Optional

from chaosprobe.metrics.latency import ONLINE_BOUTIQUE_ROUTES

# Mapping of LitmusChaos experiment names to anomaly categories
EXPERIMENT_TO_ANOMALY = {
    "pod-delete": {"category": "availability", "resource": "pod", "severity": "critical"},
    "pod-cpu-hog": {"category": "saturation", "resource": "cpu", "severity": "high"},
    "pod-memory-hog": {"category": "saturation", "resource": "memory", "severity": "high"},
    "pod-network-loss": {"category": "network", "resource": "bandwidth", "severity": "high"},
    "pod-network-latency": {"category": "network", "resource": "latency", "severity": "medium"},
    "pod-network-corruption": {
        "category": "network",
        "resource": "integrity",
        "severity": "medium",
    },
    "pod-network-duplication": {"category": "network", "resource": "bandwidth", "severity": "low"},
    "pod-io-stress": {"category": "saturation", "resource": "disk", "severity": "medium"},
    "disk-fill": {"category": "saturation", "resource": "disk", "severity": "high"},
    "node-cpu-hog": {"category": "saturation", "resource": "cpu", "severity": "critical"},
    "node-memory-hog": {"category": "saturation", "resource": "memory", "severity": "critical"},
    "node-drain": {"category": "availability", "resource": "node", "severity": "critical"},
    "kubelet-service-kill": {
        "category": "availability",
        "resource": "kubelet",
        "severity": "critical",
    },
}


def _get_affected_services(target_service: str) -> List[str]:
    """Find upstream services that depend on *target_service*.

    Uses the Online Boutique dependency graph from ``latency.py``.
    """
    affected = set()
    for src, tgt, _host, _proto, _desc in ONLINE_BOUTIQUE_ROUTES:
        if tgt == target_service and src != target_service:
            affected.add(src)
    return sorted(affected)


def generate_anomaly_labels(
    scenario: Dict[str, Any],
    metrics: Optional[Dict[str, Any]] = None,
    experiment_start: Optional[str] = None,
    experiment_end: Optional[str] = None,
    placement: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Generate structured anomaly labels for an experiment run.

    Parameters
    ----------
    scenario:
        Loaded scenario dict (from ``load_scenario``).
    metrics:
        Collected metrics dict (from ``MetricsCollector.collect``).
    experiment_start:
        ISO-8601 timestamp of chaos start. Falls back to
        ``metrics.timeWindow.start`` if not given.
    experiment_end:
        ISO-8601 timestamp of chaos end. Falls back to
        ``metrics.timeWindow.end`` if not given.
    placement:
        Placement dict with ``strategy`` and ``assignments``.

    Returns
    -------
    List of anomaly label dicts, one per experiment in the scenario.
    """
    labels: List[Dict[str, Any]] = []

    # Derive time window from metrics if not provided explicitly
    if metrics:
        tw = metrics.get("timeWindow", {})
        if experiment_start is None:
            experiment_start = tw.get("start")
        if experiment_end is None:
            experiment_end = tw.get("end")

    for exp in scenario.get("experiments", []):
        spec = exp.get("spec", {}).get("spec", {}) or exp.get("spec", {})
        appinfo = spec.get("appinfo", {})
        target_label = appinfo.get("applabel", "")
        target_service = target_label.split("=", 1)[1] if "=" in target_label else target_label
        target_ns = appinfo.get("appns", scenario.get("namespace", "default"))

        for chaos_exp in spec.get("experiments", []):
            exp_name = chaos_exp.get("name", "unknown")
            env_vars = {}
            for env in chaos_exp.get("spec", {}).get("components", {}).get("env", []):
                env_vars[env.get("name", "")] = env.get("value", "")

            # Look up anomaly metadata
            anomaly_meta = EXPERIMENT_TO_ANOMALY.get(
                exp_name,
                {
                    "category": "unknown",
                    "resource": "unknown",
                    "severity": "medium",
                },
            )

            # Determine target node from placement assignments
            target_node = None
            if placement:
                assignments = placement.get("assignments", {})
                target_node = assignments.get(target_service)

            affected = _get_affected_services(target_service)

            label: Dict[str, Any] = {
                "faultType": exp_name,
                "category": anomaly_meta["category"],
                "resource": anomaly_meta["resource"],
                "severity": anomaly_meta["severity"],
                "targetService": target_service,
                "targetNamespace": target_ns,
                "targetNode": target_node,
                "affectedServices": affected,
                "startTime": experiment_start,
                "endTime": experiment_end,
                "parameters": {
                    "duration_s": int(env_vars.get("TOTAL_CHAOS_DURATION", "0")),
                    "interval_s": int(env_vars.get("CHAOS_INTERVAL", "0")),
                    "podsAffectedPercent": int(env_vars.get("PODS_AFFECTED_PERC", "0")),
                },
            }

            # Add fault-specific parameters
            if exp_name == "pod-cpu-hog":
                label["parameters"]["cpuCores"] = int(env_vars.get("CPU_CORES", "0"))
                label["parameters"]["cpuLoad"] = int(env_vars.get("CPU_LOAD", "0"))
            elif exp_name == "pod-memory-hog":
                label["parameters"]["memoryConsumption_mb"] = int(
                    env_vars.get("MEMORY_CONSUMPTION", "0")
                )
            elif exp_name == "pod-network-loss":
                label["parameters"]["packetLossPercent"] = int(
                    env_vars.get("NETWORK_PACKET_LOSS_PERCENTAGE", "0")
                )
            elif exp_name == "pod-network-latency":
                label["parameters"]["networkLatency_ms"] = int(env_vars.get("NETWORK_LATENCY", "0"))
            elif exp_name == "pod-io-stress":
                label["parameters"]["ioWorkers"] = int(env_vars.get("NUMBER_OF_WORKERS", "0"))

            labels.append(label)

    return labels
