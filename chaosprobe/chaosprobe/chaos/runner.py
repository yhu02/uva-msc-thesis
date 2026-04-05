"""Chaos experiment runner via the ChaosCenter GraphQL API.

Accepts user-provided ChaosEngine YAML specs, registers them as
ChaosCenter experiments (Argo Workflow format), triggers execution,
and polls for completion via the ``getExperimentRun`` query.
"""

import json as _json
import time
import uuid
from copy import deepcopy
from typing import Any, Dict, List

import yaml

from chaosprobe.provisioner.setup import LitmusSetup

# Phase values returned by ChaosCenter ``getExperimentRun.phase``
_TERMINAL_PHASES = frozenset({
    "Completed",
    "Completed_With_Error",
    "Completed_With_Probe_Failure",
    "Stopped",
    "Error",
    "Timeout",
    "Terminated",
    "Skipped",
})


class ChaosRunner:
    """Runs chaos experiments exclusively through the ChaosCenter GraphQL API.

    Each ChaosEngine spec is wrapped in an Argo Workflow manifest,
    saved via ``saveChaosExperiment``, triggered via
    ``runChaosExperiment``, and monitored via ``getExperimentRun``.
    """

    def __init__(
        self,
        namespace: str,
        timeout: int = 300,
        chaoscenter: Dict[str, str] | None = None,
    ):
        """Initialise the chaos runner.

        Args:
            namespace: Namespace where experiments run.
            timeout: Timeout in seconds for experiment completion.
            chaoscenter: Dict with keys ``token``, ``project_id``,
                ``infra_id``, ``gql_url`` for the ChaosCenter API.

        Raises:
            ValueError: If *chaoscenter* is ``None`` or missing
                required keys.
        """
        if not chaoscenter:
            raise ValueError(
                "ChaosCenter configuration is required. "
                "Provide a dict with keys: token, project_id, infra_id, gql_url"
            )
        required_keys = {"token", "project_id", "infra_id", "gql_url"}
        missing = required_keys - chaoscenter.keys()
        if missing:
            raise ValueError(f"ChaosCenter config missing keys: {', '.join(sorted(missing))}")

        self.namespace = namespace
        self.timeout = timeout
        self._run_suffix = uuid.uuid4().hex[:6]
        self._cc = chaoscenter
        self._setup = LitmusSetup(skip_k8s_init=True)
        self._executed_experiments: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_experiments(self, experiments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Run all ChaosEngine experiments via ChaosCenter.

        Args:
            experiments: List of ``{file, spec}`` dicts from the scenario
                loader.  Each *spec* is a native ChaosEngine YAML dict.

        Returns:
            List of executed experiment metadata dicts.
        """
        total = len(experiments)
        for idx, exp_entry in enumerate(experiments, 1):
            engine_spec = deepcopy(exp_entry["spec"])
            filepath = exp_entry.get("file", "unknown")
            original_name = engine_spec.get("metadata", {}).get("name", "unnamed")
            print(f"  [{idx}/{total}] ChaosEngine: {original_name} (from {filepath})")
            self._run_single_experiment(engine_spec)

        return self._executed_experiments

    def get_executed_experiments(self) -> List[Dict[str, Any]]:
        """Return metadata for all executed experiments."""
        return self._executed_experiments

    # ------------------------------------------------------------------
    # Internal -- single experiment lifecycle
    # ------------------------------------------------------------------

    def _run_single_experiment(self, engine_spec: Dict[str, Any]) -> None:
        """Save, trigger, and poll a single experiment via ChaosCenter."""
        metadata = engine_spec.setdefault("metadata", {})
        original_name = metadata.get("name", "unnamed")

        # Unique name per run to avoid collisions
        engine_name = f"{original_name}-{self._run_suffix}"
        metadata["name"] = engine_name
        metadata["namespace"] = self.namespace

        # Update appinfo namespace if present
        spec = engine_spec.get("spec", {})
        appinfo = spec.get("appinfo", {})
        if appinfo:
            appinfo["appns"] = self.namespace
            spec["appinfo"] = appinfo

        spec.setdefault("annotationCheck", "false")

        exp_names = [e.get("name", "unknown") for e in spec.get("experiments", [])]

        # -- save -------------------------------------------------------
        experiment_id = str(uuid.uuid4())
        instance_id = str(uuid.uuid4())
        manifest = self._build_workflow_manifest(engine_spec, engine_name, instance_id)

        try:
            self._setup.chaoscenter_save_experiment(
                gql_url=self._cc["gql_url"],
                project_id=self._cc["project_id"],
                token=self._cc["token"],
                infra_id=self._cc["infra_id"],
                experiment_id=experiment_id,
                name=engine_name,
                manifest=manifest,
            )
            print(f"    ChaosCenter: experiment saved ({experiment_id[:8]}...)")
        except Exception as exc:
            print(f"    ERROR: Failed to save experiment: {exc}")
            self._executed_experiments.append({
                "engineName": engine_name,
                "experimentNames": exp_names,
                "status": "error",
                "error": str(exc),
            })
            return

        # -- run --------------------------------------------------------
        try:
            notify_id = self._setup.chaoscenter_run_experiment(
                gql_url=self._cc["gql_url"],
                project_id=self._cc["project_id"],
                token=self._cc["token"],
                experiment_id=experiment_id,
            )
            print(f"    ChaosCenter: run triggered (notify={notify_id[:8]}...)")
        except Exception as exc:
            print(f"    ERROR: Failed to trigger run: {exc}")
            self._executed_experiments.append({
                "engineName": engine_name,
                "experimentNames": exp_names,
                "status": "error",
                "error": str(exc),
            })
            return

        # -- poll -------------------------------------------------------
        print(f"    Waiting for experiment to complete (timeout: {self.timeout}s)...")
        start_time = time.time()
        result = self._poll_experiment_run(notify_id, start_time)

        phase = result.get("phase", "unknown")
        elapsed = int(time.time() - start_time)
        print(f"    Result: {phase} ({elapsed}s elapsed)")

        self._executed_experiments.append({
            "engineName": engine_name,
            "experimentNames": exp_names,
            "status": phase,
            "startTime": start_time,
            "endTime": time.time(),
            "resiliencyScore": result.get("resiliencyScore"),
            "faultsPassed": result.get("faultsPassed"),
            "faultsFailed": result.get("faultsFailed"),
            "totalFaults": result.get("totalFaults"),
        })

    # ------------------------------------------------------------------
    # Internal -- poll ChaosCenter for run completion
    # ------------------------------------------------------------------

    def _poll_experiment_run(
        self, notify_id: str, start_time: float
    ) -> Dict[str, Any]:
        """Poll ``getExperimentRun`` until a terminal phase or timeout."""
        last_phase = None
        while time.time() - start_time < self.timeout:
            elapsed = int(time.time() - start_time)
            try:
                run = self._setup.chaoscenter_get_experiment_run(
                    gql_url=self._cc["gql_url"],
                    project_id=self._cc["project_id"],
                    token=self._cc["token"],
                    notify_id=notify_id,
                )
            except Exception as exc:
                print(f"    [{elapsed}s] WARNING: poll failed: {exc}")
                time.sleep(5)
                continue

            phase = run.get("phase", "")
            if phase and phase != last_phase:
                print(f"    [{elapsed}s] Phase: {phase}")
                last_phase = phase

            if phase in _TERMINAL_PHASES:
                return run

            if elapsed > 0 and elapsed % 30 == 0:
                print(f"    [{elapsed}s] Still waiting...")

            time.sleep(5)

        print(f"    Timed out after {self.timeout}s")
        return {"phase": "timeout", "error": f"Timeout after {self.timeout}s"}

    # ------------------------------------------------------------------
    # Internal -- Argo Workflow manifest builder
    # ------------------------------------------------------------------

    def _build_workflow_manifest(
        self,
        engine_spec: Dict[str, Any],
        engine_name: str,
        instance_id: str,
    ) -> str:
        """Build an Argo Workflow JSON manifest wrapping a ChaosEngine spec.

        ChaosCenter parses the manifest with ``json.Unmarshal`` (Go),
        so the manifest **must** be JSON — not YAML.

        The ChaosEngine embedded in the artifact ``raw.data`` stays as
        YAML (ChaosCenter's ``processExperimentManifest`` uses
        ``yaml.Unmarshal`` for the inner engine).

        The engine must use ``generateName`` (not ``name``) so
        ChaosCenter can extract the fault name for probe/weight
        processing.
        """
        spec = engine_spec.get("spec", {})
        experiments = spec.get("experiments", [])
        fault_name = experiments[0].get("name", "unknown") if experiments else "unknown"

        # Build the inner ChaosEngine for the artifact data.
        # ChaosCenter expects ``generateName`` (not ``name``) on the engine.
        engine_copy = deepcopy(engine_spec)
        engine_meta = engine_copy.setdefault("metadata", {})
        engine_meta.setdefault("labels", {})["instance_id"] = instance_id
        # Use generateName so ChaosCenter can extract the fault name
        if "name" in engine_meta and "generateName" not in engine_meta:
            engine_meta["generateName"] = engine_meta.pop("name") + "-"
        # ChaosCenter requires a probeRef annotation; an empty list
        # signals "no probes" and avoids validation errors.
        engine_meta.setdefault("annotations", {}).setdefault("probeRef", "[]")
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
                        "metadata": {
                            "labels": {
                                "weight": "10",
                            },
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
                                "echo 'ChaosEngine cleanup delegated to pre-flight checks'",
                            ],
                        },
                    },
                ],
                "podGC": {"strategy": "OnWorkflowSuccess"},
            },
        }

        return _json.dumps(workflow)
