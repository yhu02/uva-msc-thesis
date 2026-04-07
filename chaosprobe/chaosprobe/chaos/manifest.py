"""Argo Workflow manifest builder for ChaosCenter experiments.

Constructs a JSON Argo Workflow wrapping a ChaosEngine YAML spec,
suitable for submission to the ChaosCenter GraphQL API.
"""

import json as _json
import uuid
from copy import deepcopy
from typing import Any, Dict, List

import yaml


def build_workflow_manifest(
    engine_spec: Dict[str, Any],
    engine_name: str,
    instance_id: str,
    namespace: str,
    infra_id: str,
    probe_ref: List[Dict[str, str]] | None = None,
) -> tuple[str, str]:
    """Build an Argo Workflow JSON manifest wrapping a ChaosEngine spec.

    Returns:
        Tuple of (JSON manifest string, workflow name used).

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
    # ChaosCenter requires a probeRef annotation; when probes are
    # registered via the API, reference them so the subscriber injects
    # them into the ChaosEngine at runtime.
    probe_ref_json = _json.dumps(probe_ref) if probe_ref else "[]"
    engine_meta.setdefault("annotations", {})["probeRef"] = probe_ref_json
    engine_yaml = yaml.dump(engine_copy, default_flow_style=False)

    sa = spec.get("chaosServiceAccount", "litmus-admin")
    ns = namespace

    # Build a minimal ChaosExperiment CR for the install-chaos-faults
    # template.  ChaosCenter's UI reads this to display fault metadata.
    fault_cr = {
        "apiVersion": "litmuschaos.io/v1alpha1",
        "kind": "ChaosExperiment",
        "metadata": {
            "name": fault_name,
            "namespace": ns,
        },
        "description": {"message": f"Injects {fault_name} fault"},
        "spec": {
            "definition": {
                "scope": "Namespaced",
                "permissions": [],
                "image": "litmuschaos/go-runner:latest",
                "args": [f"-name={fault_name}"],
                "command": ["/bin/bash"],
                "env": [
                    e_var
                    for exp in experiments
                    for e_var in exp.get("spec", {})
                    .get("components", {})
                    .get("env", [])
                ],
                "labels": {"name": fault_name},
            },
        },
    }
    fault_cr_yaml = yaml.dump(fault_cr, default_flow_style=False)

    # ChaosCenter appends a 14-char timestamp and Argo appends an
    # ~11-char hash to the workflow name to create the step pod name.
    # K8s labels have a 63-char limit, so the workflow name must be
    # ≤ 38 chars to keep the pod name within limits.
    wf_name = engine_name
    if len(wf_name) > 38:
        wf_name = wf_name[:31] + "-" + uuid.uuid4().hex[:6]

    workflow = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {
            "name": wf_name,
            "namespace": ns,
            "labels": {
                "infra_id": infra_id,
                "step_pod_name": "",
                "workflow_id": "",
                "subject": f"{wf_name}_{ns}",
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
                    "inputs": {},
                    "steps": [
                        [{"name": "install-chaos-faults", "template": "install-chaos-faults"}],
                        [{"name": f"run-{fault_name}", "template": f"run-{fault_name}"}],
                        [{"name": "revert-chaos", "template": "revert-chaos"}],
                    ],
                },
                {
                    "name": "install-chaos-faults",
                    "inputs": {
                        "artifacts": [
                            {
                                "name": fault_name,
                                "path": f"/tmp/{fault_name}.yaml",
                                "raw": {"data": fault_cr_yaml},
                            },
                        ],
                    },
                    "container": {
                        "image": "litmuschaos/k8s:latest",
                        "command": ["sh", "-c"],
                        "args": [
                            "echo 'ChaosExperiment already installed by chaosprobe init'",
                        ],
                    },
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
                    "inputs": {},
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

    return _json.dumps(workflow), wf_name
