"""Output generator for ChaosProbe results.

Produces structured output dicts with experiment results,
synced to Neo4j as the primary data store.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from chaosprobe.collector.result_collector import calculate_resilience_score
from chaosprobe.metrics.anomaly_labels import generate_anomaly_labels
from chaosprobe.metrics.cascade import compute_cascade_timeline
from chaosprobe.output import SCHEMA_VERSION as _SCHEMA_VERSION

_LATENCY_PHASE_NAMES = ("pre-chaos", "during-chaos", "post-chaos")


def build_route_view(
    locust_stats: Optional[Dict[str, Any]],
    latency_phases: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Join per-route Locust stats with LatencyProber phase data on route path.

    Locust is the outside-cluster view (HTTP probes from the load
    generator); LatencyProber is the inside-pod view (``kubectl exec``
    probes from every workload pod).  The two should agree per route;
    if they don't, the in-pod measurement has a measurable bias and
    that's a thesis-grade methodological finding.

    Args:
        locust_stats: The ``loadGeneration.stats`` dict from the Locust
            runner.  Per-route entries live under ``endpoints``.
            ``None`` or missing ``endpoints`` produces no Locust side.
        latency_phases: The ``metrics.latency.phases`` dict.  Each phase
            holds per-route sub-dicts keyed by route path.  ``None`` or
            missing routes produces no LatencyProber side.

    Returns:
        List of join entries, one per route that appears on *either*
        side.  Empty list when both inputs are missing or empty.

        ::

            [
                {
                    "route": "/",
                    "locust": {"p50": ..., "p95": ..., "p99": ..., "rps": ...} | None,
                    "latencyProber": {
                        "pre-chaos":    {...} | None,
                        "during-chaos": {...} | None,
                        "post-chaos":   {...} | None,
                    } | None,
                },
                ...
            ]
    """
    locust_by_route = _index_locust_endpoints(locust_stats)
    latency_by_route = _index_latency_routes(latency_phases)

    if not locust_by_route and not latency_by_route:
        return []

    # Preserve a stable order: Locust routes first (the load
    # generator's perspective), then LatencyProber-only routes
    # sorted alphabetically.
    seen: List[str] = []
    for route in locust_by_route:
        if route not in seen:
            seen.append(route)
    for route in sorted(latency_by_route):
        if route not in seen:
            seen.append(route)

    return [
        {
            "route": route,
            "locust": locust_by_route.get(route),
            "latencyProber": latency_by_route.get(route),
        }
        for route in seen
    ]


def _index_locust_endpoints(
    locust_stats: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Build a `{route_path: locust_view}` map from Locust's `endpoints` list."""
    if not locust_stats:
        return {}
    endpoints = locust_stats.get("endpoints") or []
    out: Dict[str, Dict[str, Any]] = {}
    for entry in endpoints:
        route = entry.get("name") or ""
        if not route:
            continue
        # Locust exposes p95 per-endpoint but not p50/p99 in the CSV row,
        # so we surface what we have and leave the rest unkeyed for the
        # consumer to detect.
        out[route] = {
            "requests": entry.get("requests", 0),
            "failures": entry.get("failures", 0),
            "avgResponseTime_ms": entry.get("avgResponseTime_ms"),
            "p95ResponseTime_ms": entry.get("p95ResponseTime_ms"),
        }
    return out


def _index_latency_routes(
    latency_phases: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Optional[Dict[str, Any]]]]:
    """Build a `{route_path: {phase: route_summary}}` map from latency phases.

    Routes that appear in any phase show up in the output; phases where
    the route is absent map to ``None`` so a consumer can tell "route
    didn't exist in this phase" from "route had zero samples."
    """
    if not latency_phases:
        return {}
    # Collect the union of route paths across all phases first
    routes_seen: set = set()
    phase_routes: Dict[str, Dict[str, Any]] = {}
    for phase_name in _LATENCY_PHASE_NAMES:
        phase = latency_phases.get(phase_name) or {}
        per_route = phase.get("routes") or {}
        phase_routes[phase_name] = per_route
        routes_seen.update(per_route.keys())

    out: Dict[str, Dict[str, Optional[Dict[str, Any]]]] = {}
    for route in routes_seen:
        out[route] = {phase: phase_routes[phase].get(route) for phase in _LATENCY_PHASE_NAMES}
    return out


class OutputGenerator:
    """Generates structured JSON output.

    The output includes:
    - Scenario metadata
    - Deployed infrastructure resources
    - Experiment results with probe details
    - Summary with resilience score
    """

    # Re-exported as a class attribute for backwards compatibility with
    # any caller using ``OutputGenerator.SCHEMA_VERSION``.  New code should
    # import ``chaosprobe.output.SCHEMA_VERSION`` directly.
    SCHEMA_VERSION = _SCHEMA_VERSION

    def __init__(
        self,
        scenario: Dict[str, Any],
        results: List[Dict[str, Any]],
        metrics: Optional[Dict[str, Any]] = None,
        placement: Optional[Dict[str, Any]] = None,
        service_routes: Optional[List] = None,
    ):
        """Initialize the output generator.

        Args:
            scenario: Loaded scenario from config.loader.load_scenario().
                      Contains: path, manifests, experiments, namespace.
            results: Collected experiment results from ResultCollector.
            metrics: Optional experiment metrics (recovery, pod status, etc.).
            placement: Optional placement dict with strategy and assignments.
            service_routes: Optional service dependency graph tuples.
        """
        self.scenario = scenario
        self.results = results
        self.metrics = metrics
        self.placement = placement
        self.service_routes = service_routes

    def generate(self) -> Dict[str, Any]:
        """Generate the complete AI output structure."""
        now = datetime.now(timezone.utc)
        run_id = f"run-{now.strftime('%Y-%m-%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        timestamp = now.isoformat()

        output: Dict[str, Any] = {
            "schemaVersion": self.SCHEMA_VERSION,
            "runId": run_id,
            "timestamp": timestamp,
            "scenario": self._generate_scenario_section(),
            "infrastructure": self._generate_infrastructure_section(),
            "experiments": self._generate_experiments_section(),
            "summary": self._generate_summary(),
        }

        if self.metrics:
            output["metrics"] = self.metrics

        output["anomalyLabels"] = generate_anomaly_labels(
            self.scenario,
            metrics=self.metrics,
            placement=self.placement,
            service_routes=self.service_routes,
        )

        if self.metrics and self.metrics.get("latency"):
            output["cascadeTimeline"] = compute_cascade_timeline(
                self.metrics["latency"],
                anomaly_labels=output["anomalyLabels"],
            )

        return output

    # ── Scenario section ─────────────────────────────────────

    def _generate_scenario_section(self) -> Dict[str, Any]:
        """Generate scenario metadata section with file contents."""
        manifests = []
        for m in self.scenario.get("manifests", []):
            manifests.append(
                {
                    "file": m["file"],
                    "content": m.get("spec", {}),
                }
            )

        experiments = []
        for e in self.scenario.get("experiments", []):
            experiments.append(
                {
                    "file": e["file"],
                    "content": e.get("spec", {}),
                }
            )

        return {
            "directory": self.scenario.get("path", ""),
            "manifests": manifests,
            "experiments": experiments,
        }

    # ── Infrastructure section ────────────────────────────────

    def _generate_infrastructure_section(self) -> Dict[str, Any]:
        """Generate infrastructure section."""
        return {
            "namespace": self.scenario.get("namespace", "default"),
        }

    # ── Experiments section ───────────────────────────────────

    def _generate_experiments_section(self) -> List[Dict[str, Any]]:
        """Generate experiments results section."""
        experiments = []
        for result in self.results:
            chaos_result = result.get("chaosResult", {})
            experiment = {
                "name": result.get("name", "unknown"),
                "engineName": result.get("engineName", ""),
                "result": {
                    "phase": chaos_result.get("phase", "Unknown"),
                    "verdict": result.get("verdict", "Awaited"),
                    "probeSuccessPercentage": result.get("probeSuccessPercentage", 0),
                    "failStep": chaos_result.get("failStep", ""),
                },
                "probes": chaos_result.get("probes", []),
            }
            experiments.append(experiment)
        return experiments

    # ── Summary section ───────────────────────────────────────

    def _generate_summary(self) -> Dict[str, Any]:
        """Generate summary section with probe-type breakdown."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.get("verdict") == "Pass")
        failed = sum(1 for r in self.results if r.get("verdict") == "Fail")
        resilience_score = calculate_resilience_score(self.results)
        overall_verdict = "PASS" if passed == total and total > 0 else "FAIL"

        summary: Dict[str, Any] = {
            "totalExperiments": total,
            "passed": passed,
            "failed": failed,
            "resilienceScore": resilience_score,
            "overallVerdict": overall_verdict,
        }

        # Build probe-type breakdown across all experiments
        probe_summary: Dict[str, Dict[str, int]] = {}
        for result in self.results:
            probes = result.get("chaosResult", {}).get("probes", [])
            for probe in probes:
                ptype = probe.get("type", "unknown")
                if ptype not in probe_summary:
                    probe_summary[ptype] = {"total": 0, "passed": 0, "failed": 0}
                probe_summary[ptype]["total"] += 1
                status = probe.get("status", {})
                verdict = status.get("verdict", "") if isinstance(status, dict) else ""
                if verdict == "Pass":
                    probe_summary[ptype]["passed"] += 1
                elif verdict == "Fail":
                    probe_summary[ptype]["failed"] += 1
                else:
                    # Check phaseVerdicts for per-phase results
                    phase_verdicts = probe.get("phaseVerdicts", {})
                    if phase_verdicts:
                        all_pass = all(v == "Pass" for v in phase_verdicts.values())
                        if all_pass:
                            probe_summary[ptype]["passed"] += 1
                        else:
                            probe_summary[ptype]["failed"] += 1

        if probe_summary:
            summary["probeBreakdown"] = probe_summary

        return summary
