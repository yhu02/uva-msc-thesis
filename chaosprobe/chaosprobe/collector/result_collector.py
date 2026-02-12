"""Result collector for LitmusChaos experiment results."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException


class ResultCollector:
    """Collects and processes ChaosResult CRDs from LitmusChaos experiments."""

    def __init__(self, scenario: Dict[str, Any]):
        """Initialize the result collector.

        Args:
            scenario: The scenario configuration dictionary.
        """
        self.scenario = scenario

        # Load kubernetes config
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.custom_api = client.CustomObjectsApi()

    @property
    def namespace(self) -> str:
        """Get the target namespace."""
        return self.scenario["spec"]["infrastructure"]["namespace"]

    @property
    def experiments(self) -> List[Dict[str, Any]]:
        """Get the experiment configurations."""
        return self.scenario["spec"]["experiments"]

    def collect(
        self, engine_name_map: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """Collect results for all experiments in the scenario.

        Args:
            engine_name_map: Mapping of experiment name to ChaosEngine name.
                If not provided, falls back to the default naming convention.

        Returns:
            List of experiment result dictionaries.
        """
        results = []

        for exp_config in self.experiments:
            exp_name = exp_config["name"]
            if engine_name_map and exp_name in engine_name_map:
                engine_name = engine_name_map[exp_name]
            else:
                engine_name = f"chaosprobe-{exp_name}"

            result = self._collect_experiment_result(engine_name, exp_config)
            results.append(result)

        return results

    def _collect_experiment_result(
        self, engine_name: str, exp_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Collect result for a single experiment.

        Args:
            engine_name: Name of the ChaosEngine.
            exp_config: Experiment configuration.

        Returns:
            Experiment result dictionary.
        """
        result = {
            "name": exp_config["name"],
            "type": exp_config["type"],
            "engineName": engine_name,
            "target": exp_config.get("target", {}),
        }

        # Get ChaosEngine status
        engine_status = self._get_engine_status(engine_name)
        if engine_status:
            result["engineStatus"] = engine_status

        # Get ChaosResult
        chaos_result = self._get_chaos_result(engine_name)
        if chaos_result:
            result["chaosResult"] = self._parse_chaos_result(chaos_result)

        # Calculate verdict and metrics
        result["verdict"] = self._determine_verdict(result)
        result["probeSuccessPercentage"] = self._calculate_probe_success(result)

        return result

    def _get_engine_status(self, engine_name: str) -> Optional[Dict[str, Any]]:
        """Get the status of a ChaosEngine.

        Args:
            engine_name: Name of the ChaosEngine.

        Returns:
            Engine status or None if not found.
        """
        try:
            engine = self.custom_api.get_namespaced_custom_object(
                group="litmuschaos.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="chaosengines",
                name=engine_name,
            )
            return engine.get("status", {})
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def _get_chaos_result(self, engine_name: str) -> Optional[Dict[str, Any]]:
        """Get the ChaosResult for an engine.

        Args:
            engine_name: Name of the ChaosEngine.

        Returns:
            ChaosResult or None if not found.
        """
        try:
            # ChaosResult name follows the pattern: <engine-name>-<experiment-type>
            # Try to get by that convention first, then fall back to listing by engine label
            result = self.custom_api.get_namespaced_custom_object(
                group="litmuschaos.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="chaosresults",
                name=engine_name,
            )
            return result
        except ApiException as e:
            if e.status == 404:
                # Search for ChaosResults whose spec.engine matches the engine name
                try:
                    results = self.custom_api.list_namespaced_custom_object(
                        group="litmuschaos.io",
                        version="v1alpha1",
                        namespace=self.namespace,
                        plural="chaosresults",
                    )
                    for item in results.get("items", []):
                        if item.get("spec", {}).get("engine") == engine_name:
                            return item
                    return None
                except ApiException:
                    return None
            raise

    def _parse_chaos_result(self, chaos_result: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a ChaosResult into a structured format.

        Args:
            chaos_result: Raw ChaosResult from Kubernetes API.

        Returns:
            Parsed result dictionary.
        """
        status = chaos_result.get("status", {})
        spec = chaos_result.get("spec", {})

        parsed = {
            "phase": status.get("experimentStatus", {}).get("phase", "Unknown"),
            "verdict": status.get("experimentStatus", {}).get("verdict", "Awaited"),
            "probeSuccessPercentage": self._parse_probe_success(status),
            "failStep": status.get("experimentStatus", {}).get("failStep", ""),
        }

        # Extract timing information
        history = status.get("history", {})
        if history:
            passed_runs = history.get("passedRuns", 0)
            failed_runs = history.get("failedRuns", 0)
            parsed["history"] = {
                "passedRuns": passed_runs,
                "failedRuns": failed_runs,
                "totalRuns": passed_runs + failed_runs,
            }

        # Extract probe results
        probe_statuses = status.get("experimentStatus", {}).get("probeStatuses", [])
        if probe_statuses:
            parsed["probes"] = [
                {
                    "name": p.get("name", ""),
                    "type": p.get("type", ""),
                    "status": p.get("status", {}),
                }
                for p in probe_statuses
            ]

        return parsed

    def _parse_probe_success(self, status: Dict[str, Any]) -> float:
        """Parse probe success percentage from status.

        Args:
            status: ChaosResult status.

        Returns:
            Probe success percentage (0-100).
        """
        probe_success = status.get("experimentStatus", {}).get("probeSuccessPercentage", "0%")

        if isinstance(probe_success, str):
            # Remove % sign and convert to float
            probe_success = probe_success.rstrip("%")
            try:
                return float(probe_success)
            except ValueError:
                return 0.0

        return float(probe_success)

    def _determine_verdict(self, result: Dict[str, Any]) -> str:
        """Determine the overall verdict for an experiment.

        Args:
            result: Collected experiment result.

        Returns:
            Verdict string (Pass, Fail, or Awaited).
        """
        chaos_result = result.get("chaosResult", {})

        if chaos_result:
            verdict = chaos_result.get("verdict", "Awaited")
            if verdict in ["Pass", "Fail"]:
                return verdict

        engine_status = result.get("engineStatus", {})
        if engine_status:
            experiments = engine_status.get("experiments", [])
            if experiments:
                exp_verdict = experiments[0].get("verdict", "Awaited")
                if exp_verdict in ["Pass", "Fail"]:
                    return exp_verdict

        return "Awaited"

    def _calculate_probe_success(self, result: Dict[str, Any]) -> float:
        """Calculate overall probe success percentage.

        Args:
            result: Collected experiment result.

        Returns:
            Probe success percentage (0-100).
        """
        chaos_result = result.get("chaosResult", {})
        if chaos_result:
            return chaos_result.get("probeSuccessPercentage", 0.0)

        return 0.0

    def collect_all_results(self) -> List[Dict[str, Any]]:
        """Collect all ChaosResults in the namespace.

        Returns:
            List of all ChaosResult objects.
        """
        try:
            results = self.custom_api.list_namespaced_custom_object(
                group="litmuschaos.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="chaosresults",
            )
            return results.get("items", [])
        except ApiException:
            return []

    def get_result_summary(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate a summary of collected results.

        Args:
            results: List of experiment results.

        Returns:
            Summary dictionary.
        """
        total = len(results)
        passed = sum(1 for r in results if r.get("verdict") == "Pass")
        failed = sum(1 for r in results if r.get("verdict") == "Fail")
        awaited = sum(1 for r in results if r.get("verdict") == "Awaited")

        # Calculate average probe success
        probe_successes = [r.get("probeSuccessPercentage", 0) for r in results]
        avg_probe_success = sum(probe_successes) / len(probe_successes) if probe_successes else 0

        return {
            "totalExperiments": total,
            "passed": passed,
            "failed": failed,
            "awaited": awaited,
            "averageProbeSuccess": round(avg_probe_success, 2),
            "overallVerdict": "Pass" if failed == 0 and awaited == 0 else "Fail",
        }


def calculate_resilience_score(
    results: List[Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """Calculate overall resilience score from experiment results.

    Args:
        results: List of experiment results.
        weights: Optional weights for each experiment (by name).

    Returns:
        Resilience score (0-100).
    """
    if not results:
        return 0.0

    if weights is None:
        # Equal weights for all experiments
        weights = {r["name"]: 1.0 for r in results}

    total_weight = sum(weights.get(r["name"], 1.0) for r in results)
    weighted_sum = sum(
        weights.get(r["name"], 1.0) * r.get("probeSuccessPercentage", 0)
        for r in results
    )

    return round(weighted_sum / total_weight, 2) if total_weight > 0 else 0.0
