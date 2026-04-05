"""Cascade impact analysis for fault propagation tracking.

Analyses latency time-series data to detect when downstream services
first show degradation after a fault is injected and when they recover.
This produces a ``cascadeTimeline`` that gives ML models a causal graph
of "fault at A → degradation at B after Δt".
"""

from typing import Any, Dict, List, Optional


def compute_cascade_timeline(
    latency_data: Dict[str, Any],
    anomaly_labels: Optional[List[Dict[str, Any]]] = None,
    degradation_factor: float = 2.0,
) -> Dict[str, Any]:
    """Detect cascading degradation across service routes.

    Compares during-chaos latency/error rates against pre-chaos baselines
    to identify which downstream routes were affected, when degradation
    started, and when it ended.

    Parameters
    ----------
    latency_data:
        The ``latency`` dict from experiment metrics, containing
        ``timeSeries`` and ``phases``.
    anomaly_labels:
        Anomaly labels to annotate the target service.
    degradation_factor:
        A route is degraded when its latency exceeds
        ``baseline_mean * degradation_factor``.

    Returns
    -------
    Dict with ``targetService``, ``affectedRoutes`` (each with first
    degradation time, recovery time, peak latency), and ``summary``.
    """
    time_series = latency_data.get("timeSeries", [])
    phases = latency_data.get("phases", {})

    target_service = None
    if anomaly_labels:
        target_service = anomaly_labels[0].get("targetService")

    # Compute pre-chaos baseline per route
    pre_chaos = phases.get("pre-chaos", {})
    pre_routes = pre_chaos.get("routes", {})
    baselines: Dict[str, Dict[str, float]] = {}
    for route, stats in pre_routes.items():
        mean = stats.get("mean_ms")
        if mean and mean > 0:
            baselines[route] = {
                "mean_ms": mean,
                "threshold_ms": mean * degradation_factor,
            }

    # If no pre-chaos baseline available, use a reasonable default threshold
    if not baselines:
        all_routes: set = set()
        for entry in time_series:
            all_routes.update(entry.get("routes", {}).keys())
        for route in all_routes:
            baselines[route] = {"mean_ms": 0, "threshold_ms": 500}

    # Walk time series to find degradation windows per route
    route_events: Dict[str, Dict[str, Any]] = {}
    for route in baselines:
        route_events[route] = {
            "degradationStart": None,
            "degradationEnd": None,
            "peakLatency_ms": None,
            "errorCount": 0,
            "sampleCount": 0,
            "degradedSamples": 0,
        }

    for entry in time_series:
        ts = entry.get("timestamp", "")
        phase = entry.get("phase", "")
        routes = entry.get("routes", {})

        for route, data in routes.items():
            if route not in route_events:
                route_events[route] = {
                    "degradationStart": None,
                    "degradationEnd": None,
                    "peakLatency_ms": None,
                    "errorCount": 0,
                    "sampleCount": 0,
                    "degradedSamples": 0,
                }

            info = route_events[route]
            info["sampleCount"] += 1
            lat = data.get("latency_ms")
            status = data.get("status", "ok")

            is_degraded = False
            if status != "ok":
                is_degraded = True
                info["errorCount"] += 1
            elif lat is not None and route in baselines:
                threshold = baselines[route]["threshold_ms"]
                if threshold > 0 and lat > threshold:
                    is_degraded = True

            if is_degraded:
                info["degradedSamples"] += 1
                if info["degradationStart"] is None:
                    info["degradationStart"] = ts
                # Keep extending the degradation end
                info["degradationEnd"] = ts

                if lat is not None:
                    if info["peakLatency_ms"] is None or lat > info["peakLatency_ms"]:
                        info["peakLatency_ms"] = lat
            else:
                # If we were degraded and now recovered, mark recovery
                if info["degradationStart"] is not None and info["degradationEnd"] is not None:
                    if phase == "post-chaos":
                        # Recovery happened in post-chaos
                        if "recoveryTime" not in info:
                            info["recoveryTime"] = ts

    # Build affected routes list
    affected_routes: List[Dict[str, Any]] = []
    for route, info in route_events.items():
        if info["degradedSamples"] > 0:
            baseline = baselines.get(route, {})
            entry = {
                "route": route,
                "baselineMean_ms": baseline.get("mean_ms"),
                "degradationThreshold_ms": baseline.get("threshold_ms"),
                "firstDegradation": info["degradationStart"],
                "lastDegradation": info["degradationEnd"],
                "recoveryTime": info.get("recoveryTime"),
                "peakLatency_ms": info["peakLatency_ms"],
                "errorCount": info["errorCount"],
                "degradedSamples": info["degradedSamples"],
                "totalSamples": info["sampleCount"],
                "degradationRate": (
                    round(info["degradedSamples"] / info["sampleCount"], 3)
                    if info["sampleCount"] > 0
                    else 0
                ),
            }
            affected_routes.append(entry)

    # Sort by first degradation time
    affected_routes.sort(key=lambda r: r.get("firstDegradation") or "")

    total_routes = len(route_events)
    affected_count = len(affected_routes)

    return {
        "targetService": target_service,
        "totalRoutesMonitored": total_routes,
        "affectedRoutes": affected_routes,
        "summary": {
            "totalAffected": affected_count,
            "totalMonitored": total_routes,
            "cascadeRatio": round(affected_count / total_routes, 3) if total_routes > 0 else 0,
            "degradationFactor": degradation_factor,
        },
    }
