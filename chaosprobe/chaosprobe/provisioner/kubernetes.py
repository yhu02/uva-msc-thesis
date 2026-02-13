"""Kubernetes infrastructure provisioner.

Applies and manages standard Kubernetes manifests from scenario directories.
Applies standard Kubernetes manifests from scenario directories.
"""

import time
from typing import Any, Dict, List, Optional

import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException


class KubernetesProvisioner:
    """Applies Kubernetes resource manifests from a loaded scenario."""

    def __init__(self, namespace: str):
        """Initialize the provisioner.

        Args:
            namespace: Target namespace to deploy resources into.
        """
        self.namespace = namespace

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.core_api = client.CoreV1Api()
        self.apps_api = client.AppsV1Api()
        self.networking_api = client.NetworkingV1Api()
        self.policy_api = client.PolicyV1Api()

        self._applied_resources: List[Dict[str, Any]] = []

    def provision(self, manifests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply all manifests to the cluster.

        Args:
            manifests: List of {file, spec} dicts from the scenario loader.

        Returns:
            List of applied resource metadata.
        """
        self._ensure_namespace()

        for manifest_entry in manifests:
            spec = manifest_entry["spec"]
            filepath = manifest_entry.get("file", "unknown")

            # Override namespace in the manifest
            if "metadata" in spec:
                spec["metadata"]["namespace"] = self.namespace

            self._apply_manifest(spec, filepath)

        # Wait for deployments to be ready
        self._wait_for_deployments()

        return self._applied_resources

    def get_applied_resources(self) -> List[Dict[str, Any]]:
        """Return metadata about applied resources."""
        return self._applied_resources

    def cleanup(self):
        """Delete all applied resources (in reverse order)."""
        for resource in reversed(self._applied_resources):
            self._delete_resource(resource)

    def cleanup_namespace(self):
        """Delete the entire namespace and all resources in it."""
        try:
            self.core_api.delete_namespace(
                name=self.namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground"),
            )
        except ApiException as e:
            if e.status != 404:
                raise

    def _ensure_namespace(self):
        """Create the namespace if it doesn't exist."""
        try:
            self.core_api.read_namespace(self.namespace)
        except ApiException as e:
            if e.status == 404:
                ns = client.V1Namespace(
                    metadata=client.V1ObjectMeta(
                        name=self.namespace,
                        labels={"managed-by": "chaosprobe"},
                    )
                )
                self.core_api.create_namespace(ns)
            else:
                raise

    def _apply_manifest(self, spec: Dict[str, Any], filepath: str):
        """Apply a single Kubernetes manifest.

        Uses server-side apply semantics: create or replace.
        """
        kind = spec.get("kind", "")
        name = spec.get("metadata", {}).get("name", "")
        api_version = spec.get("apiVersion", "")

        appliers = {
            "Deployment": self._apply_deployment,
            "Service": self._apply_service,
            "ConfigMap": self._apply_configmap,
            "Secret": self._apply_secret,
            "PodDisruptionBudget": self._apply_pdb,
            "NetworkPolicy": self._apply_network_policy,
        }

        applier = appliers.get(kind)
        if applier:
            applier(name, spec)
            self._applied_resources.append({
                "kind": kind,
                "name": name,
                "namespace": self.namespace,
                "file": filepath,
                "apiVersion": api_version,
            })
        else:
            print(f"    WARNING: Unsupported resource kind '{kind}' in {filepath}, skipping")

    def _apply_deployment(self, name: str, spec: Dict[str, Any]):
        """Apply a Deployment."""
        try:
            self.apps_api.read_namespaced_deployment(name, self.namespace)
            self.apps_api.replace_namespaced_deployment(name, self.namespace, spec)
        except ApiException as e:
            if e.status == 404:
                self.apps_api.create_namespaced_deployment(self.namespace, spec)
            else:
                raise

    def _apply_service(self, name: str, spec: Dict[str, Any]):
        """Apply a Service."""
        try:
            existing = self.core_api.read_namespaced_service(name, self.namespace)
            # Preserve clusterIP on update
            if existing.spec and existing.spec.cluster_ip:
                if "spec" in spec and "clusterIP" not in spec["spec"]:
                    spec["spec"]["clusterIP"] = existing.spec.cluster_ip
            self.core_api.replace_namespaced_service(name, self.namespace, spec)
        except ApiException as e:
            if e.status == 404:
                self.core_api.create_namespaced_service(self.namespace, spec)
            else:
                raise

    def _apply_configmap(self, name: str, spec: Dict[str, Any]):
        """Apply a ConfigMap."""
        try:
            self.core_api.read_namespaced_config_map(name, self.namespace)
            self.core_api.replace_namespaced_config_map(name, self.namespace, spec)
        except ApiException as e:
            if e.status == 404:
                self.core_api.create_namespaced_config_map(self.namespace, spec)
            else:
                raise

    def _apply_secret(self, name: str, spec: Dict[str, Any]):
        """Apply a Secret."""
        try:
            self.core_api.read_namespaced_secret(name, self.namespace)
            self.core_api.replace_namespaced_secret(name, self.namespace, spec)
        except ApiException as e:
            if e.status == 404:
                self.core_api.create_namespaced_secret(self.namespace, spec)
            else:
                raise

    def _apply_pdb(self, name: str, spec: Dict[str, Any]):
        """Apply a PodDisruptionBudget."""
        try:
            self.policy_api.read_namespaced_pod_disruption_budget(name, self.namespace)
            self.policy_api.replace_namespaced_pod_disruption_budget(name, self.namespace, spec)
        except ApiException as e:
            if e.status == 404:
                self.policy_api.create_namespaced_pod_disruption_budget(self.namespace, spec)
            else:
                raise

    def _apply_network_policy(self, name: str, spec: Dict[str, Any]):
        """Apply a NetworkPolicy."""
        try:
            self.networking_api.read_namespaced_network_policy(name, self.namespace)
            self.networking_api.replace_namespaced_network_policy(name, self.namespace, spec)
        except ApiException as e:
            if e.status == 404:
                self.networking_api.create_namespaced_network_policy(self.namespace, spec)
            else:
                raise

    def _delete_resource(self, resource: Dict[str, Any]):
        """Delete a single resource."""
        kind = resource["kind"]
        name = resource["name"]
        delete_opts = client.V1DeleteOptions(propagation_policy="Foreground")

        deleters = {
            "Deployment": lambda: self.apps_api.delete_namespaced_deployment(name, self.namespace, body=delete_opts),
            "Service": lambda: self.core_api.delete_namespaced_service(name, self.namespace, body=delete_opts),
            "ConfigMap": lambda: self.core_api.delete_namespaced_config_map(name, self.namespace, body=delete_opts),
            "Secret": lambda: self.core_api.delete_namespaced_secret(name, self.namespace, body=delete_opts),
            "PodDisruptionBudget": lambda: self.policy_api.delete_namespaced_pod_disruption_budget(name, self.namespace, body=delete_opts),
            "NetworkPolicy": lambda: self.networking_api.delete_namespaced_network_policy(name, self.namespace, body=delete_opts),
        }

        deleter = deleters.get(kind)
        if deleter:
            try:
                deleter()
            except ApiException as e:
                if e.status != 404:
                    raise

    def _wait_for_deployments(self, timeout: int = 120):
        """Wait for all applied Deployments to become ready."""
        deployments = [r for r in self._applied_resources if r["kind"] == "Deployment"]
        if not deployments:
            return

        start = time.time()
        for dep in deployments:
            name = dep["name"]
            while time.time() - start < timeout:
                try:
                    d = self.apps_api.read_namespaced_deployment(name, self.namespace)
                    if (
                        d.status
                        and d.status.ready_replicas
                        and d.status.ready_replicas >= (d.spec.replicas or 1)
                    ):
                        break
                except ApiException:
                    pass
                time.sleep(3)
