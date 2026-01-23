"""Chaos experiment runner for executing LitmusChaos experiments."""

import time
from typing import Any, Dict, List, Optional

import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from chaosprobe.chaos.engine import ChaosEngineGenerator


class ChaosRunner:
    """Executes LitmusChaos experiments from scenario configurations."""

    # ChaosEngine phases
    PHASE_RUNNING = "Running"
    PHASE_COMPLETED = "Completed"
    PHASE_STOPPED = "Stopped"
    PHASE_ERROR = "Error"

    def __init__(
        self,
        scenario: Dict[str, Any],
        timeout: int = 300,
        service_account: str = "litmus-admin",
    ):
        """Initialize the chaos runner.

        Args:
            scenario: The scenario configuration dictionary.
            timeout: Timeout in seconds for experiment completion.
            service_account: Service account for running experiments.
        """
        self.scenario = scenario
        self.timeout = timeout
        self.service_account = service_account

        # Load kubernetes config
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.custom_api = client.CustomObjectsApi()
        self.core_api = client.CoreV1Api()

        self._executed_experiments: List[Dict[str, Any]] = []

    @property
    def namespace(self) -> str:
        """Get the target namespace."""
        return self.scenario["spec"]["infrastructure"]["namespace"]

    @property
    def experiments(self) -> List[Dict[str, Any]]:
        """Get the experiment configurations."""
        return self.scenario["spec"]["experiments"]

    def run_experiments(self) -> List[Dict[str, Any]]:
        """Run all experiments defined in the scenario.

        Returns:
            List of executed experiment metadata.
        """
        engine_generator = ChaosEngineGenerator(self.namespace, self.service_account)

        for exp_config in self.experiments:
            engine = engine_generator.generate(exp_config)
            self._run_single_experiment(engine, exp_config)

        return self._executed_experiments

    def _run_single_experiment(
        self, engine: Dict[str, Any], exp_config: Dict[str, Any]
    ):
        """Run a single chaos experiment.

        Args:
            engine: The ChaosEngine CRD manifest.
            exp_config: The experiment configuration.
        """
        engine_name = engine["metadata"]["name"]
        exp_name = exp_config["name"]

        # Delete existing engine if present
        self._delete_chaos_engine(engine_name)

        # Create the ChaosEngine
        try:
            self.custom_api.create_namespaced_custom_object(
                group="litmuschaos.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="chaosengines",
                body=engine,
            )
        except ApiException as e:
            self._executed_experiments.append({
                "name": exp_name,
                "engineName": engine_name,
                "status": "error",
                "error": str(e),
            })
            return

        # Wait for completion
        start_time = time.time()
        final_status = self._wait_for_engine(engine_name, start_time)

        self._executed_experiments.append({
            "name": exp_name,
            "engineName": engine_name,
            "type": exp_config["type"],
            "status": final_status.get("phase", "unknown"),
            "startTime": start_time,
            "endTime": time.time(),
        })

    def _wait_for_engine(self, engine_name: str, start_time: float) -> Dict[str, Any]:
        """Wait for a ChaosEngine to complete.

        Args:
            engine_name: Name of the ChaosEngine.
            start_time: Timestamp when the experiment started.

        Returns:
            Final status of the engine.
        """
        while time.time() - start_time < self.timeout:
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

                if phase in [self.PHASE_COMPLETED, self.PHASE_STOPPED, self.PHASE_ERROR]:
                    return {"phase": phase, "status": status}

                # Also check experiment status
                experiments = status.get("experiments", [])
                if experiments:
                    exp_status = experiments[0].get("status", "")
                    if exp_status in ["Completed", "Failed", "Stopped"]:
                        return {"phase": exp_status, "status": status}

            except ApiException as e:
                if e.status == 404:
                    return {"phase": "not_found", "error": "Engine not found"}

            time.sleep(5)

        return {"phase": "timeout", "error": f"Timeout after {self.timeout}s"}

    def _delete_chaos_engine(self, engine_name: str):
        """Delete a ChaosEngine if it exists.

        Args:
            engine_name: Name of the ChaosEngine to delete.
        """
        try:
            self.custom_api.delete_namespaced_custom_object(
                group="litmuschaos.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="chaosengines",
                name=engine_name,
            )
            # Wait a bit for deletion
            time.sleep(2)
        except ApiException as e:
            if e.status != 404:
                raise

    def cleanup_engines(self):
        """Delete all ChaosEngines created by this runner."""
        for exp in self._executed_experiments:
            engine_name = exp.get("engineName")
            if engine_name:
                self._delete_chaos_engine(engine_name)

    def get_executed_experiments(self) -> List[Dict[str, Any]]:
        """Get list of executed experiments with their metadata."""
        return self._executed_experiments

    def get_engine_status(self, engine_name: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a ChaosEngine.

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
            return engine.get("status")
        except ApiException:
            return None


class ChaosEngineWatcher:
    """Watches ChaosEngine status updates."""

    def __init__(self, namespace: str):
        """Initialize the watcher.

        Args:
            namespace: Namespace to watch.
        """
        self.namespace = namespace

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.custom_api = client.CustomObjectsApi()

    def watch(self, engine_name: str, callback, timeout: int = 300):
        """Watch a ChaosEngine for status changes.

        Args:
            engine_name: Name of the ChaosEngine to watch.
            callback: Function to call on status updates.
            timeout: Watch timeout in seconds.
        """
        from kubernetes import watch

        w = watch.Watch()

        try:
            for event in w.stream(
                self.custom_api.list_namespaced_custom_object,
                group="litmuschaos.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="chaosengines",
                field_selector=f"metadata.name={engine_name}",
                timeout_seconds=timeout,
            ):
                obj = event["object"]
                event_type = event["type"]

                if obj.get("metadata", {}).get("name") == engine_name:
                    status = obj.get("status", {})
                    should_stop = callback(event_type, status)
                    if should_stop:
                        break

        finally:
            w.stop()

    def list_engines(self, label_selector: str = "managed-by=chaosprobe") -> List[Dict[str, Any]]:
        """List all ChaosEngines matching a label selector.

        Args:
            label_selector: Kubernetes label selector.

        Returns:
            List of ChaosEngine objects.
        """
        result = self.custom_api.list_namespaced_custom_object(
            group="litmuschaos.io",
            version="v1alpha1",
            namespace=self.namespace,
            plural="chaosengines",
            label_selector=label_selector,
        )
        return result.get("items", [])
