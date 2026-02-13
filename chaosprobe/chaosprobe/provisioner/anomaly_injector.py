"""Anomaly injection into Kubernetes manifests."""

from copy import deepcopy
from typing import Any, Dict, List, Optional


# Anomaly definitions with their injection logic
ANOMALY_DEFINITIONS = {
    # Pod-level anomalies
    "missing-readiness-probe": {
        "category": "pod",
        "description": "Deployment lacks readiness probe",
        "severity": "medium",
        "effect": "Pod may receive traffic before ready",
    },
    "missing-liveness-probe": {
        "category": "pod",
        "description": "Deployment lacks liveness probe",
        "severity": "high",
        "effect": "Hung pods won't be restarted",
    },
    "missing-all-probes": {
        "category": "pod",
        "description": "Deployment lacks both readiness and liveness probes",
        "severity": "critical",
        "effect": "No health checking for pods",
    },
    "no-resource-limits": {
        "category": "pod",
        "description": "Container has no resource limits",
        "severity": "high",
        "effect": "Can consume unlimited node resources",
    },
    "no-resource-requests": {
        "category": "pod",
        "description": "Container has no resource requests",
        "severity": "medium",
        "effect": "Scheduler cannot properly place pods",
    },
    "privileged-container": {
        "category": "pod",
        "description": "Container runs in privileged mode",
        "severity": "critical",
        "effect": "Container has full host access",
    },
    "run-as-root": {
        "category": "pod",
        "description": "Container runs as root user",
        "severity": "high",
        "effect": "Increased security risk",
    },
    # Deployment-level anomalies
    "insufficient-replicas": {
        "category": "deployment",
        "description": "Single replica deployment",
        "severity": "critical",
        "effect": "No redundancy during failures",
    },
    "no-pod-disruption-budget": {
        "category": "deployment",
        "description": "Missing PodDisruptionBudget",
        "severity": "medium",
        "effect": "All pods can be evicted simultaneously",
    },
    "no-anti-affinity": {
        "category": "deployment",
        "description": "No pod anti-affinity rules",
        "severity": "medium",
        "effect": "Pods may be scheduled on same node",
    },
    # Network anomalies
    "overly-permissive-network-policy": {
        "category": "network",
        "description": "NetworkPolicy allows all ingress",
        "severity": "high",
        "effect": "No network isolation",
    },
    "no-network-policy": {
        "category": "network",
        "description": "No NetworkPolicy defined",
        "severity": "medium",
        "effect": "Default allow-all network access",
    },
    # Service anomalies
    "service-selector-mismatch": {
        "category": "service",
        "description": "Service selector doesn't match pod labels",
        "severity": "critical",
        "effect": "Service has no endpoints",
    },
    "wrong-target-port": {
        "category": "service",
        "description": "Service targets wrong container port",
        "severity": "critical",
        "effect": "Service traffic not reaching application",
    },
    # Storage anomalies
    "ephemeral-storage-only": {
        "category": "storage",
        "description": "Using emptyDir without persistent storage",
        "severity": "medium",
        "effect": "Data lost on pod restart",
    },
    "no-storage-class": {
        "category": "storage",
        "description": "PVC without storage class",
        "severity": "low",
        "effect": "Uses default storage class",
    },
    # ConfigMap/Secret anomalies
    "missing-config-key": {
        "category": "config",
        "description": "Referenced config key doesn't exist",
        "severity": "critical",
        "effect": "Pod fails to start",
    },
    "empty-secret": {
        "category": "config",
        "description": "Secret has no data",
        "severity": "high",
        "effect": "Application missing credentials",
    },
}


class AnomalyInjector:
    """Injects anomalies into Kubernetes resource manifests."""

    def __init__(self):
        self.injected_anomalies: List[Dict[str, Any]] = []

    def inject(
        self,
        manifest: Dict[str, Any],
        anomaly_config: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Inject an anomaly into a manifest.

        Args:
            manifest: The Kubernetes resource manifest.
            anomaly_config: The anomaly configuration from the scenario.

        Returns:
            The modified manifest (or original if anomaly not configured/enabled).
        """
        if not anomaly_config:
            return manifest

        if not anomaly_config.get("enabled", True):
            return manifest

        anomaly_type = anomaly_config.get("type")
        if not anomaly_type:
            return manifest

        # Make a deep copy to avoid modifying the original
        modified = deepcopy(manifest)

        # Get the injection function for this anomaly type
        injector_fn = self._get_injector(anomaly_type)
        if injector_fn:
            modified = injector_fn(modified, anomaly_config)
            self.injected_anomalies.append({
                "type": anomaly_type,
                "resource": manifest.get("metadata", {}).get("name"),
                "resourceKind": manifest.get("kind"),
                **ANOMALY_DEFINITIONS.get(anomaly_type, {}),
            })

        return modified

    def _get_injector(self, anomaly_type: str):
        """Get the injection function for an anomaly type."""
        injectors = {
            "missing-readiness-probe": self._remove_readiness_probe,
            "missing-liveness-probe": self._remove_liveness_probe,
            "missing-all-probes": self._remove_all_probes,
            "no-resource-limits": self._remove_resource_limits,
            "no-resource-requests": self._remove_resource_requests,
            "privileged-container": self._add_privileged,
            "run-as-root": self._add_run_as_root,
            "insufficient-replicas": self._set_single_replica,
            "no-anti-affinity": self._remove_anti_affinity,
            "service-selector-mismatch": self._mismatch_selector,
            "wrong-target-port": self._wrong_target_port,
            "overly-permissive-network-policy": self._permissive_network_policy,
        }
        return injectors.get(anomaly_type)

    def _get_containers(self, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get containers from a manifest (handles Deployment, Pod, etc.)."""
        kind = manifest.get("kind", "")
        if kind == "Deployment":
            return manifest.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        elif kind == "Pod":
            return manifest.get("spec", {}).get("containers", [])
        return []

    def _remove_readiness_probe(
        self, manifest: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Remove readiness probes from all containers."""
        for container in self._get_containers(manifest):
            container.pop("readinessProbe", None)
        return manifest

    def _remove_liveness_probe(
        self, manifest: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Remove liveness probes from all containers."""
        for container in self._get_containers(manifest):
            container.pop("livenessProbe", None)
        return manifest

    def _remove_all_probes(
        self, manifest: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Remove all probes from all containers."""
        for container in self._get_containers(manifest):
            container.pop("readinessProbe", None)
            container.pop("livenessProbe", None)
            container.pop("startupProbe", None)
        return manifest

    def _remove_resource_limits(
        self, manifest: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Remove resource limits from all containers."""
        for container in self._get_containers(manifest):
            if "resources" in container:
                container["resources"].pop("limits", None)
        return manifest

    def _remove_resource_requests(
        self, manifest: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Remove resource requests from all containers."""
        for container in self._get_containers(manifest):
            if "resources" in container:
                container["resources"].pop("requests", None)
        return manifest

    def _add_privileged(
        self, manifest: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Add privileged security context to containers."""
        for container in self._get_containers(manifest):
            if "securityContext" not in container:
                container["securityContext"] = {}
            container["securityContext"]["privileged"] = True
        return manifest

    def _add_run_as_root(
        self, manifest: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Set containers to run as root."""
        for container in self._get_containers(manifest):
            if "securityContext" not in container:
                container["securityContext"] = {}
            container["securityContext"]["runAsUser"] = 0
            container["securityContext"]["runAsNonRoot"] = False
        return manifest

    def _set_single_replica(
        self, manifest: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Set deployment to single replica."""
        if manifest.get("kind") == "Deployment":
            manifest["spec"]["replicas"] = 1
        return manifest

    def _remove_anti_affinity(
        self, manifest: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Remove pod anti-affinity rules."""
        if manifest.get("kind") == "Deployment":
            affinity = manifest.get("spec", {}).get("template", {}).get("spec", {}).get("affinity", {})
            affinity.pop("podAntiAffinity", None)
        return manifest

    def _mismatch_selector(
        self, manifest: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Make service selector not match any pods."""
        if manifest.get("kind") == "Service":
            selector = manifest.get("spec", {}).get("selector", {})
            if selector:
                # Add a non-matching key to the selector
                first_key = list(selector.keys())[0]
                selector[first_key] = selector[first_key] + "-nonexistent"
        return manifest

    def _wrong_target_port(
        self, manifest: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Set service to target wrong port."""
        if manifest.get("kind") == "Service":
            ports = manifest.get("spec", {}).get("ports", [])
            for port in ports:
                if "targetPort" in port:
                    # Change to an unlikely port
                    port["targetPort"] = 59999
        return manifest

    def _permissive_network_policy(
        self, manifest: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Make network policy allow all traffic."""
        if manifest.get("kind") == "NetworkPolicy":
            manifest["spec"]["ingress"] = [{}]  # Empty rule = allow all
            manifest["spec"]["egress"] = [{}]
        return manifest

    def get_injected_anomalies(self) -> List[Dict[str, Any]]:
        """Get list of all injected anomalies."""
        return self.injected_anomalies

    def reset(self):
        """Reset the injected anomalies list."""
        self.injected_anomalies = []


def get_anomaly_definition(anomaly_type: str) -> Optional[Dict[str, Any]]:
    """Get the definition for an anomaly type.

    Args:
        anomaly_type: The anomaly type identifier.

    Returns:
        Anomaly definition dictionary or None if not found.
    """
    return ANOMALY_DEFINITIONS.get(anomaly_type)


def list_anomaly_types() -> List[str]:
    """List all available anomaly types.

    Returns:
        List of anomaly type identifiers.
    """
    return list(ANOMALY_DEFINITIONS.keys())
