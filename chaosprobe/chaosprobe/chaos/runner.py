"""Chaos experiment runner for native LitmusChaos ChaosEngine CRDs.

Accepts user-provided ChaosEngine YAML specs, applies them to the cluster,
monitors execution, and tracks results.  When ChaosCenter configuration is
provided, experiments are also registered via the ChaosCenter GraphQL API
so they appear in the dashboard.
"""

import time
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional

import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException


class ChaosRunner:
    """Applies native ChaosEngine CRDs and monitors their execution.

    When *chaoscenter* config is supplied (token, project_id, infra_id,
    gql_url), each experiment is saved and triggered through the
    ChaosCenter GraphQL API so that it appears in the dashboard.
    """

    PHASE_RUNNING = "Running"
    PHASE_COMPLETED = "Completed"
    PHASE_STOPPED = "Stopped"
    PHASE_ERROR = "Error"

    def __init__(
        self,
        namespace: str,
        timeout: int = 300,
        chaoscenter: Optional[Dict[str, str]] = None,
    ):
        """Initialize the chaos runner.

        Args:
            namespace: Namespace where experiments run.
            timeout: Timeout in seconds for experiment completion.
            chaoscenter: Optional dict with keys ``token``, ``project_id``,
                ``infra_id``, ``gql_url`` for ChaosCenter integration.
        """
        self.namespace = namespace
        self.timeout = timeout
        self._run_suffix = uuid.uuid4().hex[:6]
        self._cc = chaoscenter

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

        # Clean up any leftover ChaosEngines from previous iterations
        self._cleanup_managed_engines(exclude=engine_name)

        # Get experiment names from the engine spec
        exp_names = [e.get("name", "unknown") for e in spec.get("experiments", [])]

        # Register with ChaosCenter for dashboard visibility
        self._register_with_chaoscenter(engine_spec, engine_name)

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

    # -- ChaosCenter integration -------------------------------------------

    def _register_with_chaoscenter(self, engine_spec: Dict[str, Any], engine_name: str) -> None:
        """Register and trigger the experiment via ChaosCenter GraphQL API.

        Builds an Argo Workflow manifest that wraps the ChaosEngine,
        saves it as a ChaosCenter experiment, and triggers execution.
        Failures are logged but do not prevent direct CRD creation.
        """
        if not self._cc:
            return

        from chaosprobe.provisioner.setup import LitmusSetup

        experiment_id = str(uuid.uuid4())
        instance_id = str(uuid.uuid4())
        manifest = self._build_workflow_manifest(engine_spec, engine_name, instance_id)

        setup = LitmusSetup(skip_k8s_init=True)
        try:
            setup.chaoscenter_save_experiment(
                gql_url=self._cc["gql_url"],
                project_id=self._cc["project_id"],
                token=self._cc["token"],
                infra_id=self._cc["infra_id"],
                experiment_id=experiment_id,
                name=engine_name,
                manifest=manifest,
            )
            notify_id = setup.chaoscenter_run_experiment(
                gql_url=self._cc["gql_url"],
                project_id=self._cc["project_id"],
                token=self._cc["token"],
                experiment_id=experiment_id,
            )
            print(f"    ChaosCenter: experiment registered ({experiment_id[:8]}...)")
            if notify_id:
                print(f"    ChaosCenter: run triggered (notify={notify_id[:8]}...)")
        except Exception as exc:
            print(f"    ChaosCenter: WARNING - registration failed: {exc}")
            print("    ChaosCenter: experiment will still run via direct CRD")

    def _build_workflow_manifest(
        self,
        engine_spec: Dict[str, Any],
        engine_name: str,
        instance_id: str,
    ) -> str:
        """Build an Argo Workflow YAML that wraps a ChaosEngine spec.

        This is the manifest format ChaosCenter expects for experiment
        registration.  The workflow has three steps: install the
        ChaosExperiment CRD, apply the ChaosEngine (which triggers the
        actual fault), and revert (cleanup) when done.
        """
        spec = engine_spec.get("spec", {})
        experiments = spec.get("experiments", [])
        fault_name = experiments[0].get("name", "unknown") if experiments else "unknown"

        # ChaosEngine YAML with instance_id label for cleanup
        engine_copy = deepcopy(engine_spec)
        engine_copy.setdefault("metadata", {}).setdefault("labels", {})["instance_id"] = instance_id
        engine_yaml = yaml.dump(engine_copy, default_flow_style=False)

        sa = spec.get("chaosServiceAccount", "litmus-admin")
        ns = self.namespace

        workflow = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Workflow",
            "metadata": {
                "name": engine_name,
                "namespace": ns,
                "labels": {
                    "infra_id": self._cc.get("infra_id", ""),
                    "step_pod_name": "",
                    "workflow_id": "",
                    "subject": f"{engine_name}_{ns}",
                },
            },
            "spec": {
                "arguments": {
                    "parameters": [
                        {"name": "adminModeNamespace", "value": ns},
                    ],
                },
                "entrypoint": "chaos-entry",
                "securityContext": {"runAsNonRoot": True, "runAsUser": 1000},
                "serviceAccountName": sa,
                "templates": [
                    {
                        "name": "chaos-entry",
                        "steps": [
                            [{"name": f"run-{fault_name}", "template": f"run-{fault_name}"}],
                            [{"name": "revert-chaos", "template": "revert-chaos"}],
                        ],
                    },
                    {
                        "name": f"run-{fault_name}",
                        "inputs": {
                            "artifacts": [
                                {
                                    "name": fault_name,
                                    "path": f"/tmp/chaosengine-{fault_name}.yaml",
                                    "raw": {"data": engine_yaml},
                                },
                            ],
                        },
                        "container": {
                            "image": "litmuschaos/litmus-checker:latest",
                            "args": [
                                f"-file=/tmp/chaosengine-{fault_name}.yaml",
                                "-saveName=/tmp/engine-name",
                            ],
                        },
                    },
                    {
                        "name": "revert-chaos",
                        "container": {
                            "image": "litmuschaos/k8s:latest",
                            "command": ["sh", "-c"],
                            "args": [
                                f"kubectl delete chaosengine -l 'instance_id in"
                                f" ({instance_id}, )' -n"
                                " {{workflow.parameters.adminModeNamespace}}",
                            ],
                        },
                    },
                ],
                "podGC": {"strategy": "OnWorkflowCompletion"},
            },
        }

        return yaml.dump(workflow, default_flow_style=False)
