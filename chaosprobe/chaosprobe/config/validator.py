"""Validation for ChaosProbe scenarios.

Validates that a scenario directory contains valid Kubernetes manifests
and at least one well-formed ChaosEngine definition, including full
validation of all LitmusChaos resilience probe types.
"""

from typing import Any, Dict, List, Optional

# All supported LitmusChaos resilience probe types
VALID_PROBE_TYPES = {"httpProbe", "cmdProbe", "k8sProbe", "promProbe"}

# All supported probe execution modes
VALID_PROBE_MODES = {"SOT", "EOT", "Edge", "Continuous", "OnChaos"}

# k8sProbe supported operations
VALID_K8S_OPERATIONS = {"create", "delete", "present", "absent"}

# Comparator criteria for cmdProbe / promProbe
VALID_COMPARATOR_CRITERIA_INT = {">=", "<=", ">", "<", "==", "!=", "oneOf", "between"}
VALID_COMPARATOR_CRITERIA_STRING = {
    "equal",
    "notEqual",
    "contains",
    "matches",
    "notMatches",
    "oneOf",
}

# httpProbe criteria
VALID_HTTP_CRITERIA = {"==", "!=", "oneOf"}


class ValidationError(Exception):
    """Exception raised when scenario validation fails."""

    def __init__(self, message: str, errors: Optional[List[str]] = None):
        self.errors = errors or []
        detail = "; ".join(self.errors) if self.errors else ""
        full_msg = f"{message}: {detail}" if detail else message
        super().__init__(full_msg)


def validate_scenario(scenario: Dict[str, Any]) -> bool:
    """Validate a loaded scenario.

    Args:
        scenario: Scenario dict from load_scenario().

    Returns:
        True if validation passes.

    Raises:
        ValidationError: If validation fails.
    """
    errors: List[str] = []

    # Must have experiments
    experiments = scenario.get("experiments", [])
    if not experiments:
        errors.append("Scenario must contain at least one ChaosEngine")

    # Validate each ChaosEngine
    for exp in experiments:
        spec = exp.get("spec", {})
        exp_errors = _validate_chaos_engine(spec, exp.get("file", "unknown"))
        errors.extend(exp_errors)

    # Validate K8s manifests (basic checks)
    for manifest in scenario.get("manifests", []):
        spec = manifest.get("spec", {})
        m_errors = _validate_manifest(spec, manifest.get("file", "unknown"))
        errors.extend(m_errors)

    # Validate cluster config if present
    cluster = scenario.get("cluster")
    if cluster:
        c_errors = _validate_cluster_config(cluster)
        errors.extend(c_errors)

    if errors:
        raise ValidationError("Scenario validation failed", errors)

    return True


def _validate_chaos_engine(spec: Dict[str, Any], filepath: str) -> List[str]:
    """Validate a ChaosEngine spec."""
    errors = []

    if spec.get("apiVersion") != "litmuschaos.io/v1alpha1":
        errors.append(f"{filepath}: ChaosEngine apiVersion must be litmuschaos.io/v1alpha1")

    if spec.get("kind") != "ChaosEngine":
        errors.append(f"{filepath}: kind must be ChaosEngine")

    engine_spec = spec.get("spec", {})
    if not engine_spec:
        errors.append(f"{filepath}: ChaosEngine spec is empty")
        return errors

    # Must have experiments list
    experiments = engine_spec.get("experiments", [])
    if not experiments:
        errors.append(f"{filepath}: ChaosEngine must define at least one experiment")

    # Must have appinfo for pod-level experiments
    appinfo = engine_spec.get("appinfo", {})
    if not appinfo.get("applabel"):
        errors.append(f"{filepath}: ChaosEngine spec.appinfo.applabel is required")

    # Validate chaosServiceAccount
    if not engine_spec.get("chaosServiceAccount"):
        errors.append(f"{filepath}: ChaosEngine spec.chaosServiceAccount is required")

    # Validate probes in each experiment
    for exp in experiments:
        exp_spec = exp.get("spec", {})
        probes = exp_spec.get("probe", [])
        exp_name = exp.get("name", "unknown")
        for probe in probes:
            p_errors = _validate_probe(probe, filepath, exp_name)
            errors.extend(p_errors)

    return errors


def _validate_probe(probe: Dict[str, Any], filepath: str, exp_name: str) -> List[str]:
    """Validate a single resilience probe definition.

    Supports all LitmusChaos probe types: httpProbe, cmdProbe, k8sProbe, promProbe.
    """
    errors = []
    prefix = f"{filepath}[{exp_name}]"

    # Name is required
    probe_name = probe.get("name")
    if not probe_name:
        errors.append(f"{prefix}: probe missing 'name'")
        return errors

    prefix = f"{filepath}[{exp_name}].probe[{probe_name}]"

    # Type is required and must be valid
    probe_type = probe.get("type")
    if not probe_type:
        errors.append(f"{prefix}: probe missing 'type'")
        return errors
    if probe_type not in VALID_PROBE_TYPES:
        errors.append(
            f"{prefix}: invalid probe type '{probe_type}', "
            f"must be one of {sorted(VALID_PROBE_TYPES)}"
        )
        return errors

    # Mode is required and must be valid
    mode = probe.get("mode")
    if not mode:
        errors.append(f"{prefix}: probe missing 'mode'")
    elif mode not in VALID_PROBE_MODES:
        errors.append(
            f"{prefix}: invalid probe mode '{mode}', " f"must be one of {sorted(VALID_PROBE_MODES)}"
        )

    # runProperties is required
    run_props = probe.get("runProperties")
    if not run_props:
        errors.append(f"{prefix}: probe missing 'runProperties'")
    else:
        rp_errors = _validate_run_properties(run_props, prefix)
        errors.extend(rp_errors)

    # Validate type-specific inputs
    if probe_type == "httpProbe":
        errors.extend(_validate_http_probe(probe, prefix))
    elif probe_type == "cmdProbe":
        errors.extend(_validate_cmd_probe(probe, prefix))
    elif probe_type == "k8sProbe":
        errors.extend(_validate_k8s_probe(probe, prefix))
    elif probe_type == "promProbe":
        errors.extend(_validate_prom_probe(probe, prefix))

    return errors


def _validate_run_properties(run_props: Dict[str, Any], prefix: str) -> List[str]:
    """Validate probe runProperties."""
    errors = []
    if not run_props.get("probeTimeout"):
        errors.append(f"{prefix}: runProperties.probeTimeout is required")
    if not run_props.get("interval"):
        errors.append(f"{prefix}: runProperties.interval is required")
    if run_props.get("retry") is None:
        errors.append(f"{prefix}: runProperties.retry is required")
    return errors


def _validate_http_probe(probe: Dict[str, Any], prefix: str) -> List[str]:
    """Validate httpProbe-specific inputs."""
    errors = []
    inputs = probe.get("httpProbe/inputs")
    if not inputs:
        errors.append(f"{prefix}: httpProbe/inputs is required")
        return errors

    if not inputs.get("url"):
        errors.append(f"{prefix}: httpProbe/inputs.url is required")

    method = inputs.get("method")
    if not method:
        errors.append(f"{prefix}: httpProbe/inputs.method is required")
        return errors

    if "get" in method:
        get_cfg = method["get"]
        if not get_cfg.get("criteria"):
            errors.append(f"{prefix}: httpProbe/inputs.method.get.criteria is required")
        elif get_cfg["criteria"] not in VALID_HTTP_CRITERIA:
            errors.append(
                f"{prefix}: invalid get criteria '{get_cfg['criteria']}', "
                f"must be one of {sorted(VALID_HTTP_CRITERIA)}"
            )
        if not get_cfg.get("responseCode"):
            errors.append(f"{prefix}: httpProbe/inputs.method.get.responseCode is required")
    elif "post" in method:
        post_cfg = method["post"]
        if not post_cfg.get("contentType"):
            errors.append(f"{prefix}: httpProbe/inputs.method.post.contentType is required")
        if not post_cfg.get("body") and not post_cfg.get("bodyPath"):
            errors.append(f"{prefix}: httpProbe/inputs.method.post requires 'body' or 'bodyPath'")
        if post_cfg.get("body") and post_cfg.get("bodyPath"):
            errors.append(
                f"{prefix}: httpProbe/inputs.method.post 'body' and 'bodyPath'"
                f" are mutually exclusive"
            )
        if not post_cfg.get("criteria"):
            errors.append(f"{prefix}: httpProbe/inputs.method.post.criteria is required")
        if not post_cfg.get("responseCode"):
            errors.append(f"{prefix}: httpProbe/inputs.method.post.responseCode is required")
    else:
        errors.append(f"{prefix}: httpProbe/inputs.method must contain 'get' or 'post'")

    return errors


def _validate_cmd_probe(probe: Dict[str, Any], prefix: str) -> List[str]:
    """Validate cmdProbe-specific inputs."""
    errors = []
    inputs = probe.get("cmdProbe/inputs")
    if not inputs:
        errors.append(f"{prefix}: cmdProbe/inputs is required")
        return errors

    if not inputs.get("command"):
        errors.append(f"{prefix}: cmdProbe/inputs.command is required")

    comparator = inputs.get("comparator")
    if not comparator:
        errors.append(f"{prefix}: cmdProbe/inputs.comparator is required")
    else:
        errors.extend(_validate_comparator(comparator, prefix))

    # source is optional; if present, validate image
    source = inputs.get("source")
    if source and isinstance(source, dict):
        if not source.get("image"):
            errors.append(
                f"{prefix}: cmdProbe/inputs.source.image is required when source is specified"
            )

    return errors


def _validate_k8s_probe(probe: Dict[str, Any], prefix: str) -> List[str]:
    """Validate k8sProbe-specific inputs."""
    errors = []
    inputs = probe.get("k8sProbe/inputs")
    if not inputs:
        errors.append(f"{prefix}: k8sProbe/inputs is required")
        return errors

    if inputs.get("group") is None:
        errors.append(f"{prefix}: k8sProbe/inputs.group is required")
    if not inputs.get("version"):
        errors.append(f"{prefix}: k8sProbe/inputs.version is required")
    if not inputs.get("resource"):
        errors.append(f"{prefix}: k8sProbe/inputs.resource is required")
    if not inputs.get("namespace"):
        errors.append(f"{prefix}: k8sProbe/inputs.namespace is required")

    operation = inputs.get("operation")
    if not operation:
        errors.append(f"{prefix}: k8sProbe/inputs.operation is required")
    elif operation not in VALID_K8S_OPERATIONS:
        errors.append(
            f"{prefix}: invalid k8sProbe operation '{operation}', "
            f"must be one of {sorted(VALID_K8S_OPERATIONS)}"
        )

    # create operation requires data
    if operation == "create" and not probe.get("data"):
        errors.append(f"{prefix}: k8sProbe 'data' field is required for 'create' operation")

    return errors


def _validate_prom_probe(probe: Dict[str, Any], prefix: str) -> List[str]:
    """Validate promProbe-specific inputs."""
    errors = []
    inputs = probe.get("promProbe/inputs")
    if not inputs:
        errors.append(f"{prefix}: promProbe/inputs is required")
        return errors

    if not inputs.get("endpoint"):
        errors.append(f"{prefix}: promProbe/inputs.endpoint is required")

    if not inputs.get("query") and not inputs.get("queryPath"):
        errors.append(f"{prefix}: promProbe/inputs requires 'query' or 'queryPath'")
    if inputs.get("query") and inputs.get("queryPath"):
        errors.append(f"{prefix}: promProbe/inputs 'query' and 'queryPath' are mutually exclusive")

    comparator = inputs.get("comparator")
    if not comparator:
        errors.append(f"{prefix}: promProbe/inputs.comparator is required")
    else:
        errors.extend(_validate_comparator(comparator, prefix))

    return errors


def _validate_comparator(comparator: Dict[str, Any], prefix: str) -> List[str]:
    """Validate a comparator block used by cmdProbe and promProbe."""
    errors = []
    comp_type = comparator.get("type")
    if not comp_type:
        errors.append(f"{prefix}: comparator.type is required")
    elif comp_type not in ("string", "int", "float"):
        errors.append(f"{prefix}: comparator.type must be 'string', 'int', or 'float'")

    criteria = comparator.get("criteria")
    if not criteria:
        errors.append(f"{prefix}: comparator.criteria is required")
    elif comp_type in ("int", "float") and criteria not in VALID_COMPARATOR_CRITERIA_INT:
        errors.append(f"{prefix}: invalid comparator criteria '{criteria}' for type '{comp_type}'")
    elif comp_type == "string" and criteria not in VALID_COMPARATOR_CRITERIA_STRING:
        errors.append(f"{prefix}: invalid comparator criteria '{criteria}' for type 'string'")

    if comparator.get("value") is None:
        errors.append(f"{prefix}: comparator.value is required")

    return errors


def _validate_manifest(spec: Dict[str, Any], filepath: str) -> List[str]:
    """Validate a Kubernetes manifest (basic checks)."""
    errors = []

    if not spec.get("apiVersion"):
        errors.append(f"{filepath}: manifest missing apiVersion")

    if not spec.get("kind"):
        errors.append(f"{filepath}: manifest missing kind")

    if not spec.get("metadata", {}).get("name"):
        errors.append(f"{filepath}: manifest missing metadata.name")

    return errors


def _validate_cluster_config(cluster: Dict[str, Any]) -> List[str]:
    """Validate the cluster configuration section of a scenario."""
    errors = []

    workers = cluster.get("workers", {})
    if not workers:
        errors.append("cluster: workers section is required")
        return errors

    count = workers.get("count")
    if count is not None:
        if not isinstance(count, int) or count < 1:
            errors.append("cluster.workers.count must be a positive integer")

    cpu = workers.get("cpu")
    if cpu is not None:
        if not isinstance(cpu, int) or cpu < 1:
            errors.append("cluster.workers.cpu must be a positive integer")

    memory = workers.get("memory")
    if memory is not None:
        if not isinstance(memory, int) or memory < 256:
            errors.append("cluster.workers.memory must be an integer >= 256 (MB)")

    disk = workers.get("disk")
    if disk is not None:
        if not isinstance(disk, int) or disk < 1:
            errors.append("cluster.workers.disk must be a positive integer (GB)")

    # Validate optional provider field
    provider = cluster.get("provider")
    if provider is not None and provider not in ("vagrant", "kubespray"):
        errors.append(f"cluster.provider must be 'vagrant' or 'kubespray', got '{provider}'")

    return errors
