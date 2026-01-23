"""AI-consumable output generator for ChaosProbe results."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from chaosprobe.collector.result_collector import calculate_resilience_score
from chaosprobe.provisioner.anomaly_injector import ANOMALY_DEFINITIONS


class OutputGenerator:
    """Generates structured JSON output for AI consumption."""

    SCHEMA_VERSION = "1.0.0"

    def __init__(
        self,
        scenario: Dict[str, Any],
        results: List[Dict[str, Any]],
        with_anomaly: bool = True,
        injected_anomalies: Optional[List[Dict[str, Any]]] = None,
    ):
        """Initialize the output generator.

        Args:
            scenario: The scenario configuration dictionary.
            results: Collected experiment results.
            with_anomaly: Whether anomalies were injected.
            injected_anomalies: List of injected anomalies.
        """
        self.scenario = scenario
        self.results = results
        self.with_anomaly = with_anomaly
        self.injected_anomalies = injected_anomalies or []

    def generate(self) -> Dict[str, Any]:
        """Generate the complete AI output structure.

        Returns:
            Structured output dictionary.
        """
        run_id = f"run-{datetime.utcnow().strftime('%Y-%m-%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        timestamp = datetime.utcnow().isoformat() + "Z"

        output = {
            "schemaVersion": self.SCHEMA_VERSION,
            "runId": run_id,
            "timestamp": timestamp,
            "scenario": self._generate_scenario_metadata(),
            "infrastructure": self._generate_infrastructure_section(),
            "experiments": self._generate_experiments_section(),
            "summary": self._generate_summary(),
            "aiAnalysisHints": self._generate_analysis_hints(),
        }

        return output

    def generate_minimal(self) -> Dict[str, Any]:
        """Generate minimal output format for quick AI consumption.

        Returns:
            Minimal output dictionary.
        """
        summary = self._generate_summary()
        anomaly = self._get_primary_anomaly()

        return {
            "runId": f"run-{datetime.utcnow().strftime('%Y-%m-%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
            "verdict": summary["overallVerdict"],
            "resilienceScore": summary["resilienceScore"],
            "issueDetected": summary["overallVerdict"] == "FAIL",
            "anomaly": {
                "type": anomaly.get("type") if anomaly else None,
                "likelyRootCause": anomaly is not None and self.with_anomaly,
            } if anomaly else None,
            "actionRequired": summary["overallVerdict"] == "FAIL",
        }

    def _generate_scenario_metadata(self) -> Dict[str, Any]:
        """Generate scenario metadata section."""
        metadata = self.scenario.get("metadata", {})
        return {
            "name": metadata.get("name", "unknown"),
            "description": metadata.get("description", ""),
        }

    def _generate_infrastructure_section(self) -> Dict[str, Any]:
        """Generate infrastructure section."""
        infra_config = self.scenario["spec"]["infrastructure"]

        resources = []
        for resource in infra_config.get("resources", []):
            resource_info = {
                "name": resource["name"],
                "type": resource["type"],
                "status": "deployed",
            }

            # Add anomaly information
            anomaly = resource.get("anomaly")
            if anomaly and anomaly.get("enabled", True) and self.with_anomaly:
                anomaly_type = anomaly.get("type")
                anomaly_def = ANOMALY_DEFINITIONS.get(anomaly_type, {})
                resource_info["anomaly"] = {
                    "enabled": True,
                    "type": anomaly_type,
                    "description": anomaly.get("description", anomaly_def.get("description", "")),
                    "severity": anomaly_def.get("severity", "unknown"),
                    "effect": anomaly_def.get("effect", ""),
                }
            else:
                resource_info["anomaly"] = None

            resources.append(resource_info)

        return {
            "namespace": infra_config["namespace"],
            "resources": resources,
            "anomalyInjected": self.with_anomaly,
        }

    def _generate_experiments_section(self) -> List[Dict[str, Any]]:
        """Generate experiments section."""
        experiments = []
        success_criteria = self.scenario["spec"].get("successCriteria", {})
        experiment_criteria = {
            c["experimentName"]: c
            for c in success_criteria.get("experimentCriteria", [])
        }

        for result in self.results:
            exp_name = result["name"]
            criteria = experiment_criteria.get(exp_name, {})

            experiment = {
                "name": exp_name,
                "type": result.get("type", "unknown"),
                "target": result.get("target", {}),
                "timing": self._extract_timing(result),
                "result": {
                    "phase": result.get("chaosResult", {}).get("phase", "Unknown"),
                    "verdict": result.get("verdict", "Awaited"),
                    "probeSuccessPercentage": result.get("probeSuccessPercentage", 0),
                    "failStep": result.get("chaosResult", {}).get("failStep", ""),
                    "history": result.get("chaosResult", {}).get("history", {}),
                },
                "probes": self._extract_probe_details(result),
                "criteria": {
                    "expected": {
                        "minProbeSuccessPercentage": criteria.get("minProbeSuccessPercentage", 0),
                        "expectedVerdict": criteria.get("expectedVerdict", "Pass"),
                    },
                    "actual": {
                        "probeSuccessPercentage": result.get("probeSuccessPercentage", 0),
                        "verdict": result.get("verdict", "Awaited"),
                    },
                    "met": self._check_criteria_met(result, criteria),
                },
            }

            experiments.append(experiment)

        return experiments

    def _generate_summary(self) -> Dict[str, Any]:
        """Generate summary section."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.get("verdict") == "Pass")
        failed = sum(1 for r in self.results if r.get("verdict") == "Fail")

        resilience_score = calculate_resilience_score(self.results)
        overall_verdict = "PASS" if failed == 0 else "FAIL"

        success_criteria = self.scenario["spec"].get("successCriteria", {})
        min_resilience = success_criteria.get("minResilienceScore", 0)
        require_all_pass = success_criteria.get("requireAllPass", True)

        # Evaluate criteria
        criteria_evaluation = {
            "minResilienceScore": {
                "required": min_resilience,
                "actual": resilience_score,
                "met": resilience_score >= min_resilience,
            },
            "requireAllPass": {
                "required": require_all_pass,
                "actual": failed == 0,
                "met": not require_all_pass or failed == 0,
            },
        }

        # Override verdict based on criteria
        if not criteria_evaluation["minResilienceScore"]["met"]:
            overall_verdict = "FAIL"
        if require_all_pass and failed > 0:
            overall_verdict = "FAIL"

        return {
            "totalExperiments": total,
            "passed": passed,
            "failed": failed,
            "resilienceScore": resilience_score,
            "overallVerdict": overall_verdict,
            "criteriaEvaluation": criteria_evaluation,
        }

    def _generate_analysis_hints(self) -> Dict[str, Any]:
        """Generate AI analysis hints section."""
        hints = {
            "primaryIssue": None,
            "anomalyCorrelation": None,
            "suggestedFixes": [],
        }

        # Find the primary issue
        failed_experiments = [r for r in self.results if r.get("verdict") == "Fail"]
        if failed_experiments:
            primary_failure = failed_experiments[0]
            fail_step = primary_failure.get("chaosResult", {}).get("failStep", "")
            hints["primaryIssue"] = self._describe_failure(primary_failure, fail_step)

        # Correlate with anomaly
        if self.with_anomaly:
            anomaly = self._get_primary_anomaly()
            if anomaly and failed_experiments:
                correlation = self._correlate_anomaly_with_failure(
                    anomaly, failed_experiments[0]
                )
                hints["anomalyCorrelation"] = correlation

                # Generate suggested fixes
                hints["suggestedFixes"] = self._generate_suggested_fixes(anomaly)

        return hints

    def _extract_timing(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Extract timing information from result."""
        chaos_result = result.get("chaosResult", {})
        return {
            "startedAt": chaos_result.get("startTime", ""),
            "completedAt": chaos_result.get("endTime", ""),
            "durationSeconds": chaos_result.get("totalDuration", 0),
        }

    def _extract_probe_details(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract probe details from result."""
        probes = result.get("chaosResult", {}).get("probes", [])
        return [
            {
                "name": p.get("name", ""),
                "type": p.get("type", ""),
                "status": p.get("status", {}),
            }
            for p in probes
        ]

    def _check_criteria_met(
        self, result: Dict[str, Any], criteria: Dict[str, Any]
    ) -> bool:
        """Check if experiment criteria are met."""
        if not criteria:
            return result.get("verdict") == "Pass"

        min_probe_success = criteria.get("minProbeSuccessPercentage", 0)
        expected_verdict = criteria.get("expectedVerdict", "Pass")

        actual_probe_success = result.get("probeSuccessPercentage", 0)
        actual_verdict = result.get("verdict", "Awaited")

        return (
            actual_probe_success >= min_probe_success
            and actual_verdict == expected_verdict
        )

    def _get_primary_anomaly(self) -> Optional[Dict[str, Any]]:
        """Get the primary injected anomaly."""
        for resource in self.scenario["spec"]["infrastructure"].get("resources", []):
            anomaly = resource.get("anomaly")
            if anomaly and anomaly.get("enabled", True):
                return {
                    "type": anomaly.get("type"),
                    "resource": resource["name"],
                    "resourceType": resource["type"],
                    **ANOMALY_DEFINITIONS.get(anomaly.get("type"), {}),
                }
        return None

    def _describe_failure(self, result: Dict[str, Any], fail_step: str) -> str:
        """Generate a human-readable description of the failure."""
        exp_type = result.get("type", "unknown")
        probe_success = result.get("probeSuccessPercentage", 0)

        if fail_step:
            return f"Experiment '{exp_type}' failed at step '{fail_step}' with {probe_success}% probe success"
        else:
            return f"Experiment '{exp_type}' failed with {probe_success}% probe success"

    def _correlate_anomaly_with_failure(
        self, anomaly: Dict[str, Any], failure: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Correlate an anomaly with an experiment failure."""
        anomaly_type = anomaly.get("type", "")
        exp_type = failure.get("type", "")

        # Simple correlation logic based on anomaly and experiment types
        correlation_map = {
            ("missing-readiness-probe", "pod-delete"): {
                "likelyContributed": True,
                "confidence": 0.85,
                "reasoning": "HTTP probe failures occurred during pod termination, suggesting traffic was routed to terminating pods due to lack of readiness probe",
            },
            ("missing-liveness-probe", "pod-cpu-hog"): {
                "likelyContributed": True,
                "confidence": 0.80,
                "reasoning": "Hung pods were not restarted during CPU stress due to missing liveness probe",
            },
            ("insufficient-replicas", "pod-delete"): {
                "likelyContributed": True,
                "confidence": 0.90,
                "reasoning": "Single replica deployment caused complete service unavailability when pod was deleted",
            },
            ("no-resource-limits", "pod-memory-hog"): {
                "likelyContributed": True,
                "confidence": 0.75,
                "reasoning": "Memory hog experiment consumed all available memory due to missing resource limits",
            },
            ("service-selector-mismatch", "pod-delete"): {
                "likelyContributed": True,
                "confidence": 0.95,
                "reasoning": "Service had no endpoints due to selector mismatch, causing all requests to fail",
            },
        }

        default_correlation = {
            "likelyContributed": True,
            "confidence": 0.60,
            "reasoning": f"Anomaly '{anomaly_type}' may have contributed to experiment '{exp_type}' failure",
        }

        correlation = correlation_map.get(
            (anomaly_type, exp_type), default_correlation
        )

        return {
            "anomalyType": anomaly_type,
            **correlation,
        }

    def _generate_suggested_fixes(
        self, anomaly: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Generate suggested fixes for an anomaly."""
        fixes_map = {
            "missing-readiness-probe": {
                "priority": 1,
                "fix": "Add readiness probe to deployment",
                "expectedImprovement": "Prevent traffic to unready pods",
                "configChange": {
                    "path": "spec.template.spec.containers[0].readinessProbe",
                    "value": {
                        "httpGet": {"path": "/", "port": 80},
                        "initialDelaySeconds": 5,
                        "periodSeconds": 5,
                    },
                },
            },
            "missing-liveness-probe": {
                "priority": 1,
                "fix": "Add liveness probe to deployment",
                "expectedImprovement": "Automatically restart hung pods",
                "configChange": {
                    "path": "spec.template.spec.containers[0].livenessProbe",
                    "value": {
                        "httpGet": {"path": "/", "port": 80},
                        "initialDelaySeconds": 10,
                        "periodSeconds": 10,
                    },
                },
            },
            "insufficient-replicas": {
                "priority": 1,
                "fix": "Increase replica count to at least 2",
                "expectedImprovement": "Provide redundancy during failures",
                "configChange": {
                    "path": "spec.replicas",
                    "value": 3,
                },
            },
            "no-resource-limits": {
                "priority": 2,
                "fix": "Add resource limits to containers",
                "expectedImprovement": "Prevent unbounded resource consumption",
                "configChange": {
                    "path": "spec.template.spec.containers[0].resources.limits",
                    "value": {"cpu": "500m", "memory": "256Mi"},
                },
            },
            "no-pod-disruption-budget": {
                "priority": 2,
                "fix": "Create PodDisruptionBudget",
                "expectedImprovement": "Prevent simultaneous pod eviction",
                "configChange": {
                    "path": "new:PodDisruptionBudget",
                    "value": {
                        "minAvailable": 1,
                        "selector": {"matchLabels": {"app": "target-app"}},
                    },
                },
            },
            "service-selector-mismatch": {
                "priority": 1,
                "fix": "Fix service selector to match pod labels",
                "expectedImprovement": "Service will have endpoints and route traffic correctly",
                "configChange": {
                    "path": "spec.selector",
                    "value": "Match pod labels exactly",
                },
            },
        }

        anomaly_type = anomaly.get("type", "")
        fix = fixes_map.get(anomaly_type)

        if fix:
            return [fix]

        return []
