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


class OutputGenerator:
    """Generates structured JSON output.

    The output includes:
    - Scenario metadata
    - Deployed infrastructure resources
    - Experiment results with probe details
    - Summary with resilience score
    """

    SCHEMA_VERSION = "2.0.0"

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

        output = {
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

    def generate_minimal(self) -> Dict[str, Any]:
        """Generate minimal output for quick AI consumption."""
        summary = self._generate_summary()
        return {
            "runId": f"run-{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
            "verdict": summary["overallVerdict"],
            "resilienceScore": summary["resilienceScore"],
            "issueDetected": summary["overallVerdict"] == "FAIL",
        }

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
