"""Validation for ChaosProbe scenarios.

Validates that a scenario directory contains valid Kubernetes manifests
and at least one well-formed ChaosEngine definition.
"""

from typing import Any, Dict, List


class ValidationError(Exception):
    """Exception raised when scenario validation fails."""

    def __init__(self, message: str, errors: List[str] = None):
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
