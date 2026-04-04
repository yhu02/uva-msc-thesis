"""Chaos experiment runner for native LitmusChaos ChaosEngine CRDs.

Accepts user-provided ChaosEngine YAML specs, applies them to the cluster,
monitors execution, and tracks results.  If the scenario contains Rust
cmdProbe sources, they are compiled and packaged before the experiments are
submitted.
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

    def build_rust_probes(
        self,
        scenario: Dict[str, Any],
        registry: str = "chaosprobe",
        load_kind: bool = False,
    ) -> Dict[str, str]:
        """Build Rust cmdProbe binaries and patch experiment specs.

        Discovers Rust probe sources in the scenario's ``probes/``
        directory, compiles them to static Linux binaries, builds
        container images, and patches the corresponding ``cmdProbe``
        ``source.image`` fields in the experiment specs.

        Args:
            scenario: Loaded scenario dict (must have ``path`` key and
                optionally ``probes`` key from the loader).
            registry: Container registry prefix for image tags.
            load_kind: Load built images into a local kind cluster.

        Returns:
            Mapping of probe name → image tag.  Empty dict if no Rust
            probes were found.
        """
        rust_probes = scenario.get("probes", [])
        if not rust_probes:
            return {}

        from chaosprobe.probes.builder import RustProbeBuilder, patch_probe_images

        builder = RustProbeBuilder(registry=registry, load_kind=load_kind)
        built_images = builder.build_all(scenario["path"])

        if built_images:
            patched = patch_probe_images(scenario["experiments"], built_images)
            if patched:
                print(f"  Patched {patched} cmdProbe(s) with built images")

        return built_images

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

        # Clean up any leftover ChaosEngines from previous iterations
        self._cleanup_managed_engines(exclude=engine_name)

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
            self._executed_experiments.append(
                {
                    "engineName": engine_name,
                    "experimentNames": exp_names,
                    "status": "error",
                    "error": str(e),
                }
            )
            return

        # Wait for completion
        print(f"    Waiting for experiment to complete (timeout: {self.timeout}s)...")
        start_time = time.time()
        final_status = self._wait_for_engine(engine_name, start_time)

        phase = final_status.get("phase", "unknown")
        elapsed = int(time.time() - start_time)
        print(f"    Result: {phase} ({elapsed}s elapsed)")

        self._executed_experiments.append(
            {
                "engineName": engine_name,
                "experimentNames": exp_names,
                "status": phase,
                "startTime": start_time,
                "endTime": time.time(),
            }
        )

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
                    # LitmusChaos uses several terminal states:
                    # Completed, Completed_With_Probe_Failure,
                    # Completed_With_Error, Failed, Stopped
                    if exp_status.startswith("Completed") or exp_status in ["Failed", "Stopped"]:
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

    def _cleanup_managed_engines(self, exclude: str = "") -> None:
        """Delete all ChaosEngines labelled managed-by=chaosprobe.

        This prevents leftover engines from previous iterations from
        interfering with the current experiment. The LitmusChaos operator
        can get stuck when multiple engines exist in the same namespace.

        Args:
            exclude: Engine name to skip (the one we're about to create).
        """
        try:
            engines = self.custom_api.list_namespaced_custom_object(
                group="litmuschaos.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="chaosengines",
                label_selector="managed-by=chaosprobe",
            )
        except ApiException:
            return

        for engine in engines.get("items", []):
            name = engine.get("metadata", {}).get("name", "")
            if name and name != exclude:
                print(f"    Cleaning up leftover ChaosEngine '{name}'...")
                self._delete_chaos_engine(name)

    def get_executed_experiments(self) -> List[Dict[str, Any]]:
        """Get list of executed experiments with their metadata."""
        return self._executed_experiments
