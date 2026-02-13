"""Chaos experiment runner for native LitmusChaos ChaosEngine CRDs.

Accepts user-provided ChaosEngine YAML specs, applies them to the cluster,
monitors execution, and tracks results.
"""

import time
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException


class ChaosRunner:
    """Applies native ChaosEngine CRDs and monitors their execution."""

    PHASE_RUNNING = "Running"
    PHASE_COMPLETED = "Completed"
    PHASE_STOPPED = "Stopped"
    PHASE_ERROR = "Error"

    def __init__(self, namespace: str, timeout: int = 300):
        """Initialize the chaos runner.

        Args:
            namespace: Namespace where experiments run.
            timeout: Timeout in seconds for experiment completion.
        """
        self.namespace = namespace
        self.timeout = timeout
        self._run_suffix = uuid.uuid4().hex[:6]

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.custom_api = client.CustomObjectsApi()
        self._executed_experiments: List[Dict[str, Any]] = []

    def run_experiments(self, experiments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Run all ChaosEngine experiments.

        Args:
            experiments: List of {file, spec} dicts from the scenario loader.
                Each spec is a native ChaosEngine YAML dict.

        Returns:
            List of executed experiment metadata.
        """
        total = len(experiments)
        for idx, exp_entry in enumerate(experiments, 1):
            engine_spec = deepcopy(exp_entry["spec"])
            filepath = exp_entry.get("file", "unknown")

            original_name = engine_spec.get("metadata", {}).get("name", "unnamed")
            print(f"  [{idx}/{total}] ChaosEngine: {original_name} (from {filepath})")

            self._run_single_experiment(engine_spec)

        return self._executed_experiments

    def _run_single_experiment(self, engine_spec: Dict[str, Any]):
        """Run a single ChaosEngine experiment.

        Patches the spec to:
        - Set namespace to self.namespace
        - Add unique suffix to name to avoid conflicts
        - Update appinfo.appns to match namespace
        """
        metadata = engine_spec.setdefault("metadata", {})
        original_name = metadata.get("name", "unnamed")

        # Add unique suffix
        engine_name = f"{original_name}-{self._run_suffix}"
        metadata["name"] = engine_name
        metadata["namespace"] = self.namespace
        metadata.setdefault("labels", {})["managed-by"] = "chaosprobe"

        # Update appinfo namespace if present
        spec = engine_spec.get("spec", {})
        appinfo = spec.get("appinfo", {})
        if appinfo:
            appinfo["appns"] = self.namespace
            spec["appinfo"] = appinfo

        # Ensure annotationCheck is false (avoids needing annotations on target)
        spec.setdefault("annotationCheck", "false")

        # Delete existing engine if present
        self._delete_chaos_engine(engine_name)

        # Get experiment names from the engine spec
        exp_names = [e.get("name", "unknown") for e in spec.get("experiments", [])]

        # Create the ChaosEngine
        print(f"    Creating ChaosEngine '{engine_name}'...")
        try:
            self.custom_api.create_namespaced_custom_object(
                group="litmuschaos.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="chaosengines",
                body=engine_spec,
            )
        except ApiException as e:
            print(f"    ERROR: Failed to create ChaosEngine: {e.reason}")
            self._executed_experiments.append({
                "engineName": engine_name,
                "experimentNames": exp_names,
                "status": "error",
                "error": str(e),
            })
            return

        # Wait for completion
        print(f"    Waiting for experiment to complete (timeout: {self.timeout}s)...")
        start_time = time.time()
        final_status = self._wait_for_engine(engine_name, start_time)

        phase = final_status.get("phase", "unknown")
        elapsed = int(time.time() - start_time)
        print(f"    Result: {phase} ({elapsed}s elapsed)")

        self._executed_experiments.append({
            "engineName": engine_name,
            "experimentNames": exp_names,
            "status": phase,
            "startTime": start_time,
            "endTime": time.time(),
        })

    def _wait_for_engine(self, engine_name: str, start_time: float) -> Dict[str, Any]:
        """Wait for a ChaosEngine to complete."""
        last_phase = None
        while time.time() - start_time < self.timeout:
            elapsed = int(time.time() - start_time)
            try:
                engine = self.custom_api.get_namespaced_custom_object(
                    group="litmuschaos.io",
                    version="v1alpha1",
                    namespace=self.namespace,
                    plural="chaosengines",
                    name=engine_name,
                )

                status = engine.get("status", {})
                phase = status.get("engineStatus", "")

                if phase and phase != last_phase:
                    print(f"    [{elapsed}s] Engine status: {phase}")
                    last_phase = phase

                if phase in [self.PHASE_COMPLETED, self.PHASE_STOPPED, self.PHASE_ERROR]:
                    return {"phase": phase, "status": status}

                experiments = status.get("experiments", [])
                if experiments:
                    exp_status = experiments[0].get("status", "")
                    exp_verdict = experiments[0].get("verdict", "")
                    if exp_status and exp_status != last_phase:
                        detail = f" (verdict: {exp_verdict})" if exp_verdict else ""
                        print(f"    [{elapsed}s] Experiment status: {exp_status}{detail}")
                        last_phase = exp_status
                    if exp_status in ["Completed", "Failed", "Stopped"]:
                        return {"phase": exp_status, "status": status}

            except ApiException as e:
                if e.status == 404:
                    print(f"    [{elapsed}s] Engine not found")
                    return {"phase": "not_found", "error": "Engine not found"}

            if elapsed > 0 and elapsed % 30 == 0:
                print(f"    [{elapsed}s] Still waiting...")

            time.sleep(5)

        print(f"    Timed out after {self.timeout}s")
        return {"phase": "timeout", "error": f"Timeout after {self.timeout}s"}

    def _delete_chaos_engine(self, engine_name: str):
        """Delete a ChaosEngine if it exists, waiting for full removal."""
        try:
            self.custom_api.delete_namespaced_custom_object(
                group="litmuschaos.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="chaosengines",
                name=engine_name,
            )
            print(f"    Deleting previous ChaosEngine '{engine_name}'...")
        except ApiException as e:
            if e.status == 404:
                return
            raise

        max_wait = 60
        start = time.time()
        while time.time() - start < max_wait:
            try:
                self.custom_api.get_namespaced_custom_object(
                    group="litmuschaos.io",
                    version="v1alpha1",
                    namespace=self.namespace,
                    plural="chaosengines",
                    name=engine_name,
                )
                time.sleep(2)
            except ApiException as e:
                if e.status == 404:
                    return
                raise

        # Force-remove finalizers if stuck
        print(f"    Engine still has finalizers after {max_wait}s, force-removing...")
        try:
            self.custom_api.patch_namespaced_custom_object(
                group="litmuschaos.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="chaosengines",
                name=engine_name,
                body={"metadata": {"finalizers": []}},
            )
            time.sleep(2)
        except ApiException as e:
            if e.status != 404:
                raise

    def get_executed_experiments(self) -> List[Dict[str, Any]]:
        """Get list of executed experiments with their metadata."""
        return self._executed_experiments
