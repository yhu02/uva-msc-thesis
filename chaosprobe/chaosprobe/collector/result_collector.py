"""Result collector for LitmusChaos experiment results.

Collects ChaosResult CRDs based on engine names from the runner.
"""

from typing import Any, Dict, List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException


class ResultCollector:
    """Collects and processes ChaosResult CRDs from LitmusChaos experiments."""

    def __init__(self, namespace: str):
        """Initialize the result collector.

        Args:
            namespace: Namespace to collect results from.
        """
        self.namespace = namespace

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.custom_api = client.CustomObjectsApi()

    def collect(self, executed_experiments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collect results for all executed experiments.

        Args:
            executed_experiments: List from ChaosRunner.get_executed_experiments().

        Returns:
            List of experiment result dictionaries.
        """
        results = []

        for exp_info in executed_experiments:
            engine_name = exp_info.get("engineName", "")
            exp_names = exp_info.get("experimentNames", [])

            for exp_name in exp_names:
                result = self._collect_experiment_result(engine_name, exp_name)
                results.append(result)

        return results

    def _collect_experiment_result(
        self, engine_name: str, experiment_name: str
    ) -> Dict[str, Any]:
        """Collect result for a single experiment."""
        result: Dict[str, Any] = {
            "name": experiment_name,
            "engineName": engine_name,
        }

        # Get ChaosEngine status
        engine_status = self._get_engine_status(engine_name)
        if engine_status:
            result["engineStatus"] = engine_status

        # Get ChaosResult
        chaos_result = self._get_chaos_result(engine_name, experiment_name)
        if chaos_result:
            result["chaosResult"] = self._parse_chaos_result(chaos_result)

        # Calculate verdict and metrics
        result["verdict"] = self._determine_verdict(result)
        result["probeSuccessPercentage"] = self._calculate_probe_success(result)

        return result

    def _get_engine_status(self, engine_name: str) -> Optional[Dict[str, Any]]:
        """Get the status of a ChaosEngine."""
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

    def _get_chaos_result(
        self, engine_name: str, experiment_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get the ChaosResult for an experiment.

        ChaosResult name follows the pattern: <engine-name>-<experiment-type>.
        Falls back to searching by spec.engine field.
        """
        # Try exact name match: <engine-name>-<experiment-name>
        result_name = f"{engine_name}-{experiment_name}"
        try:
            result = self.custom_api.get_namespaced_custom_object(
                group="litmuschaos.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="chaosresults",
                name=result_name,
            )
            return result
        except ApiException as e:
            if e.status != 404:
                raise

        # Try engine name directly
        try:
            result = self.custom_api.get_namespaced_custom_object(
                group="litmuschaos.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="chaosresults",
                name=engine_name,
            )
            return result
        except ApiException as e:
            if e.status != 404:
                raise

        # Fallback: search by spec.engine field
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

    def _parse_chaos_result(self, chaos_result: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a ChaosResult into a structured format."""
        status = chaos_result.get("status", {})

        parsed: Dict[str, Any] = {
            "phase": status.get("experimentStatus", {}).get("phase", "Unknown"),
            "verdict": status.get("experimentStatus", {}).get("verdict", "Awaited"),
            "probeSuccessPercentage": self._parse_probe_success(status),
            "failStep": status.get("experimentStatus", {}).get("failStep", ""),
        }

        history = status.get("history", {})
        if history:
            passed_runs = history.get("passedRuns", 0)
            failed_runs = history.get("failedRuns", 0)
            parsed["history"] = {
                "passedRuns": passed_runs,
                "failedRuns": failed_runs,
                "totalRuns": passed_runs + failed_runs,
            }

        probe_statuses = status.get("probeStatuses", [])
        if probe_statuses:
            parsed["probes"] = [
                {
                    "name": p.get("name", ""),
                    "type": p.get("type", ""),
                    "mode": p.get("mode", ""),
                    "status": p.get("status", {}),
                }
                for p in probe_statuses
            ]

        return parsed

    def _parse_probe_success(self, status: Dict[str, Any]) -> float:
        """Parse probe success percentage from status."""
        probe_success = status.get("experimentStatus", {}).get(
            "probeSuccessPercentage", "0%"
        )
        if isinstance(probe_success, str):
            probe_success = probe_success.rstrip("%")
            try:
                return float(probe_success)
            except ValueError:
                return 0.0
        return float(probe_success)

    def _determine_verdict(self, result: Dict[str, Any]) -> str:
        """Determine the overall verdict for an experiment."""
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
        """Calculate overall probe success percentage."""
        chaos_result = result.get("chaosResult", {})
        if chaos_result:
            return chaos_result.get("probeSuccessPercentage", 0.0)
        return 0.0


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
        weights = {r["name"]: 1.0 for r in results}

    total_weight = sum(weights.get(r["name"], 1.0) for r in results)
    weighted_sum = sum(
        weights.get(r["name"], 1.0) * r.get("probeSuccessPercentage", 0)
        for r in results
    )

    return round(weighted_sum / total_weight, 2) if total_weight > 0 else 0.0
