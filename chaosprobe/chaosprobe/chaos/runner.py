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

from chaosprobe.chaos.manifest import build_workflow_manifest
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

# How many times to re-trigger an experiment after TARGET_SELECTION_ERROR
_MAX_TARGET_RETRIES = 2


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
        self._cc = chaoscenter
        self._setup = LitmusSetup(skip_k8s_init=True)
        self._executed_experiments: List[Dict[str, Any]] = []
        self._registered_probes: set[str] = set()

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
        """Save, trigger, and poll a single experiment via ChaosCenter.

        If the experiment fails with a ``TARGET_SELECTION_ERROR`` (pod
        temporarily unavailable), it is re-triggered up to
        ``_MAX_TARGET_RETRIES`` times after waiting for the target pod
        to recover.
        """
        metadata = engine_spec.setdefault("metadata", {})
        original_name = metadata.get("name", "unnamed")

        # Stable name per experiment — reuses the same ChaosCenter entry
        engine_name = original_name
        metadata["name"] = engine_name
        metadata["namespace"] = self.namespace

        # Update appinfo namespace if present
        spec = engine_spec.get("spec", {})
        appinfo = spec.get("appinfo", {})
        if appinfo:
            appinfo["appns"] = self.namespace
            spec["appinfo"] = appinfo

        spec.setdefault("annotationCheck", "false")
        spec.setdefault("jobCleanUpPolicy", "delete")

        # Register inline probes with ChaosCenter and build probeRef list
        probe_ref = self._register_and_extract_probes(engine_spec)

        exp_names = [e.get("name", "unknown") for e in spec.get("experiments", [])]

        # -- save -------------------------------------------------------
        # Deterministic experiment_id so the same experiment name always
        # maps to the same ChaosCenter entry (update, not duplicate).
        # instance_id must be unique per run so revert-chaos cleanup
        # from one workflow never deletes ChaosEngines from another.
        _ns = uuid.UUID("d7e1f2a0-1234-5678-9abc-def012345678")
        experiment_id = str(uuid.uuid5(_ns, engine_name))
        instance_id = str(uuid.uuid4())
        manifest, wf_name = self._build_workflow_manifest(
            engine_spec, engine_name, instance_id, probe_ref=probe_ref,
        )

        try:
            experiment_id = self._setup.chaoscenter_save_experiment(
                gql_url=self._cc["gql_url"],
                project_id=self._cc["project_id"],
                token=self._cc["token"],
                infra_id=self._cc["infra_id"],
                experiment_id=experiment_id,
                name=wf_name,
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

        # -- run + poll (with retry on TARGET_SELECTION_ERROR) -----------
        result = self._run_and_poll(experiment_id, exp_names, engine_name, engine_spec)

        phase = result.get("phase", "unknown")
        entry = {
            "engineName": engine_name,
            "experimentNames": exp_names,
            "status": phase,
            "startTime": result.get("startTime", time.time()),
            "endTime": time.time(),
            "resiliencyScore": result.get("resiliencyScore"),
            "faultsPassed": result.get("faultsPassed"),
            "faultsFailed": result.get("faultsFailed"),
            "totalFaults": result.get("totalFaults"),
        }
        if "error" in result:
            entry["error"] = result["error"]
        self._executed_experiments.append(entry)

    # ------------------------------------------------------------------
    # Internal -- run + poll with retry on TARGET_SELECTION_ERROR
    # ------------------------------------------------------------------

    def _run_and_poll(
        self,
        experiment_id: str,
        exp_names: List[str],
        engine_name: str,
        engine_spec: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Trigger, poll, and retry on TARGET_SELECTION_ERROR.

        The go-runner selects target pods at ChaosInject time, which may
        be minutes after our readiness check.  If the target pod
        restarted in between, the go-runner fails with
        ``TARGET_SELECTION_ERROR``.  We wait for the pod to recover and
        re-trigger the same (already saved) experiment.
        """
        appinfo = engine_spec.get("spec", {}).get("appinfo", {})
        target_label = appinfo.get("applabel", "")
        # Extract deployment name from label like "app=productcatalogservice"
        target_deployment = target_label.split("=", 1)[1] if "=" in target_label else ""

        for attempt in range(_MAX_TARGET_RETRIES + 1):
            try:
                notify_id = self._setup.chaoscenter_run_experiment(
                    gql_url=self._cc["gql_url"],
                    project_id=self._cc["project_id"],
                    token=self._cc["token"],
                    experiment_id=experiment_id,
                )
                suffix = f" (attempt {attempt + 1})" if attempt > 0 else ""
                print(f"    ChaosCenter: run triggered (notify={notify_id[:8]}...){suffix}")
            except Exception as exc:
                print(f"    ERROR: Failed to trigger run: {exc}")
                return {"phase": "error", "error": str(exc)}

            start_time = time.time()
            print(f"    Waiting for experiment to complete (timeout: {self.timeout}s)...")
            result = self._poll_experiment_run(notify_id, start_time)

            phase = result.get("phase", "unknown")
            elapsed = int(time.time() - start_time)
            print(f"    Result: {phase} ({elapsed}s elapsed)")

            # Only retry on execution-level errors (e.g. target pod
            # unavailable), NOT on probe failures ("Completed_With_Error"
            # with a resiliencyScore > 0 means probes ran but some failed).
            resiliency = result.get("resiliencyScore")
            is_execution_error = (
                phase == "Error"
                or (phase == "Completed_With_Error" and resiliency in (None, 0, 0.0))
            )
            if is_execution_error and attempt < _MAX_TARGET_RETRIES and target_deployment:
                print(f"    Execution error detected, waiting for target pod to recover...")
                if self._wait_for_target_recovery(target_deployment, timeout=90):
                    print(f"    Target pod recovered, re-triggering experiment...")
                    continue
                else:
                    print(f"    Target pod did not recover, giving up.")

            result["startTime"] = start_time
            return result

        return result  # type: ignore[possibly-undefined]

    def _wait_for_target_recovery(
        self, deployment_name: str, timeout: int = 90,
    ) -> bool:
        """Wait until the target deployment has a Running pod."""
        from kubernetes import client as k8s_client

        core = k8s_client.CoreV1Api()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                pods = core.list_namespaced_pod(
                    self.namespace,
                    label_selector=f"app={deployment_name}",
                )
                running = [
                    p for p in pods.items
                    if p.status and p.status.phase == "Running"
                    and all(
                        cs.ready for cs in (p.status.container_statuses or [])
                    )
                ]
                if running:
                    return True
            except Exception:
                pass
            time.sleep(5)
        return False

    # ------------------------------------------------------------------
    # Internal -- poll ChaosCenter for run completion
    # ------------------------------------------------------------------

    def _poll_experiment_run(
        self, notify_id: str, start_time: float
    ) -> Dict[str, Any]:
        """Poll ``getExperimentRun`` until a terminal phase or timeout."""
        last_phase = None
        last_heartbeat = start_time
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
                last_heartbeat = time.time()

            if phase in _TERMINAL_PHASES:
                return run

            now = time.time()
            if now - last_heartbeat >= 30:
                print(f"    [{elapsed}s] Still running...")
                last_heartbeat = now

            time.sleep(5)

        print(f"    Timed out after {self.timeout}s")
        return {"phase": "timeout", "error": f"Timeout after {self.timeout}s"}

    # ------------------------------------------------------------------
    # Internal -- register probes with ChaosCenter API
    # ------------------------------------------------------------------

    def _register_and_extract_probes(
        self, engine_spec: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """Register inline probes with ChaosCenter and return probeRef list.

        Extracts probe definitions from the ChaosEngine experiments,
        registers each as a ChaosCenter Resilience Probe via the
        ``addProbe`` mutation, then builds a ``probeRef`` list so
        ChaosCenter can map probe verdicts back to the experiment.

        Inline probes are **kept** in the engine spec so the go-runner
        evaluates them directly and writes verdicts to the ChaosResult
        CR.  The ``probeRef`` annotation tells ChaosCenter which
        registered probes to expect results for.

        Returns:
            List of ``{"probeID": name, "mode": mode}`` dicts for the
            ``probeRef`` annotation.
        """
        experiments = (
            engine_spec.get("spec", {}).get("experiments", [])
        )
        if not experiments:
            return []

        probe_refs: List[Dict[str, str]] = []

        for exp in experiments:
            inline_probes = exp.get("spec", {}).get("probe", [])
            if not inline_probes:
                continue

            for probe_def in inline_probes:
                name = probe_def.get("name", "")
                probe_type = probe_def.get("type", "")
                mode = probe_def.get("mode", "Continuous")

                if not name or not probe_type:
                    continue

                # Only register once per runner session
                registered = name in self._registered_probes
                if not registered:
                    try:
                        api_request = self._probe_to_api_request(probe_def)
                        self._setup.chaoscenter_add_probe(
                            gql_url=self._cc["gql_url"],
                            project_id=self._cc["project_id"],
                            token=self._cc["token"],
                            probe_request=api_request,
                        )
                        self._registered_probes.add(name)
                        registered = True
                        print(f"    Registered probe: {name} ({probe_type}/{mode})")
                    except Exception as exc:
                        # Probe may already exist from a previous run — update it
                        err_msg = str(exc).lower()
                        if "already" in err_msg or "duplicate" in err_msg or "exists" in err_msg:
                            try:
                                self._setup.chaoscenter_update_probe(
                                    gql_url=self._cc["gql_url"],
                                    project_id=self._cc["project_id"],
                                    token=self._cc["token"],
                                    probe_request=api_request,
                                )
                                print(f"    Updated probe: {name} ({probe_type}/{mode})")
                            except Exception as update_exc:
                                print(f"    Probe exists, update failed: {update_exc}")
                            self._registered_probes.add(name)
                            registered = True
                        else:
                            print(f"    WARNING: Failed to register probe '{name}': {exc}")

                if registered:
                    probe_refs.append({"probeID": name, "mode": mode})

        # Inline probes stay in the engine spec for go-runner evaluation;
        # probeRef tells ChaosCenter which registered probes to track.
        return probe_refs

    @staticmethod
    def _probe_to_api_request(probe_def: Dict[str, Any]) -> Dict[str, Any]:
        """Convert an inline ChaosEngine probe to a ChaosCenter API ProbeRequest."""
        name = probe_def["name"]
        probe_type = probe_def["type"]
        run_props = probe_def.get("runProperties", {})

        base_props: Dict[str, Any] = {
            "probeTimeout": run_props.get("probeTimeout", "5s"),
            "interval": run_props.get("interval", "2s"),
            "retry": int(run_props.get("retry", 1)),
            "attempt": int(run_props.get("attempt", 1)),
            "probePollingInterval": run_props.get("probePollingInterval", "2s"),
            "initialDelay": run_props.get("initialDelay", "0s"),
            "evaluationTimeout": run_props.get("evaluationTimeout", "0s"),
            "stopOnFailure": bool(run_props.get("stopOnFailure", False)),
        }

        request: Dict[str, Any] = {
            "name": name,
            "type": probe_type,
            "infrastructureType": "Kubernetes",
        }

        if probe_type == "httpProbe":
            http_inputs = probe_def.get("httpProbe/inputs", {})
            method_def = http_inputs.get("method", {})
            method_req: Dict[str, Any] = {}
            if "get" in method_def:
                method_req["get"] = {
                    "criteria": method_def["get"].get("criteria", "=="),
                    "responseCode": str(method_def["get"].get("responseCode", "200")),
                }
            elif "post" in method_def:
                post = method_def["post"]
                method_req["post"] = {
                    "criteria": post.get("criteria", "=="),
                    "responseCode": str(post.get("responseCode", "200")),
                }
                if "contentType" in post:
                    method_req["post"]["contentType"] = post["contentType"]
                if "body" in post:
                    method_req["post"]["body"] = post["body"]

            request["kubernetesHTTPProperties"] = {
                **base_props,
                "url": http_inputs.get("url", ""),
                "method": method_req,
                "insecureSkipVerify": bool(http_inputs.get("insecureSkipVerify", False)),
            }

        elif probe_type == "cmdProbe":
            cmd_inputs = probe_def.get("cmdProbe/inputs", {})
            comparator = cmd_inputs.get("comparator", {})
            request["kubernetesCMDProperties"] = {
                **base_props,
                "command": cmd_inputs.get("command", ""),
                "comparator": {
                    "type": comparator.get("type", "string"),
                    "value": str(comparator.get("value", "")),
                    "criteria": comparator.get("criteria", "=="),
                },
            }
            if "source" in cmd_inputs:
                request["kubernetesCMDProperties"]["source"] = cmd_inputs["source"]

        elif probe_type == "promProbe":
            prom_inputs = probe_def.get("promProbe/inputs", {})
            comparator = prom_inputs.get("comparator", {})
            request["promProperties"] = {
                **base_props,
                "endpoint": prom_inputs.get("endpoint", ""),
                "comparator": {
                    "type": comparator.get("type", "float"),
                    "value": str(comparator.get("value", "")),
                    "criteria": comparator.get("criteria", ">="),
                },
            }
            if "query" in prom_inputs:
                request["promProperties"]["query"] = prom_inputs["query"]
            if "queryPath" in prom_inputs:
                request["promProperties"]["queryPath"] = prom_inputs["queryPath"]

        elif probe_type == "k8sProbe":
            k8s_inputs = probe_def.get("k8sProbe/inputs", {})
            request["k8sProperties"] = {
                **base_props,
                "version": k8s_inputs.get("version", "v1"),
                "resource": k8s_inputs.get("resource", ""),
                "operation": k8s_inputs.get("operation", "present"),
            }
            for key in ("group", "namespace", "resourceNames",
                        "fieldSelector", "labelSelector"):
                if key in k8s_inputs:
                    request["k8sProperties"][key] = k8s_inputs[key]

        return request

    # ------------------------------------------------------------------
    # Internal -- Argo Workflow manifest builder
    # ------------------------------------------------------------------

    def _build_workflow_manifest(
        self,
        engine_spec: Dict[str, Any],
        engine_name: str,
        instance_id: str,
        probe_ref: List[Dict[str, str]] | None = None,
    ) -> tuple[str, str]:
        """Build an Argo Workflow JSON manifest wrapping a ChaosEngine spec.

        Delegates to :func:`chaosprobe.chaos.manifest.build_workflow_manifest`.
        """
        return build_workflow_manifest(
            engine_spec=engine_spec,
            engine_name=engine_name,
            instance_id=instance_id,
            namespace=self.namespace,
            infra_id=self._cc.get("infra_id", ""),
            probe_ref=probe_ref,
        )
