"""Kubernetes infrastructure provisioner."""

import time
from typing import Any, Dict, List, Optional

import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from chaosprobe.provisioner.anomaly_injector import AnomalyInjector


class KubernetesProvisioner:
    """Provisions Kubernetes resources from scenario configurations."""

    def __init__(self, scenario: Dict[str, Any]):
        """Initialize the provisioner.

        Args:
            scenario: The scenario configuration dictionary.
        """
        self.scenario = scenario
        self.anomaly_injector = AnomalyInjector()

        # Load kubernetes config
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.core_api = client.CoreV1Api()
        self.apps_api = client.AppsV1Api()
        self.networking_api = client.NetworkingV1Api()
        self.policy_api = client.PolicyV1Api()
        self.autoscaling_api = client.AutoscalingV1Api()

        self._provisioned_resources: List[Dict[str, Any]] = []

    @property
    def namespace(self) -> str:
        """Get the target namespace."""
        return self.scenario["spec"]["infrastructure"]["namespace"]

    @property
    def resources(self) -> List[Dict[str, Any]]:
        """Get the resource configurations."""
        return self.scenario["spec"]["infrastructure"]["resources"]

    def generate_manifests(self) -> List[str]:
        """Generate YAML manifests for all resources.

        Returns:
            List of YAML manifest strings.
        """
        manifests = []

        for resource_config in self.resources:
            manifest = self._generate_manifest(resource_config)
            if manifest:
                manifests.append(yaml.dump(manifest, default_flow_style=False))

        return manifests

    def provision(self) -> List[Dict[str, Any]]:
        """Provision all resources in the cluster.

        Returns:
            List of provisioned resource metadata.
        """
        self._ensure_namespace()

        for resource_config in self.resources:
            manifest = self._generate_manifest(resource_config)
            if manifest:
                self._apply_manifest(manifest)

        # Wait for deployments to be ready
        self._wait_for_deployments()

        return self._provisioned_resources

    def cleanup(self):
        """Delete all provisioned resources."""
        for resource in reversed(self._provisioned_resources):
            self._delete_resource(resource)

    def cleanup_namespace(self):
        """Delete the entire namespace and all resources in it."""
        try:
            self.core_api.delete_namespace(
                name=self.namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground")
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
                        labels={"managed-by": "chaosprobe"}
                    )
                )
                self.core_api.create_namespace(ns)
            else:
                raise

    def _generate_manifest(self, resource_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Generate a Kubernetes manifest from resource configuration.

        Args:
            resource_config: The resource configuration from the scenario.

        Returns:
            Kubernetes manifest dictionary.
        """
        resource_type = resource_config["type"]
        name = resource_config["name"]
        spec = resource_config.get("spec", {})
        anomaly = resource_config.get("anomaly")

        generators = {
            "deployment": self._generate_deployment,
            "service": self._generate_service,
            "configmap": self._generate_configmap,
            "secret": self._generate_secret,
            "pdb": self._generate_pdb,
            "networkpolicy": self._generate_network_policy,
            "pvc": self._generate_pvc,
            "hpa": self._generate_hpa,
            "ingress": self._generate_ingress,
        }

        generator = generators.get(resource_type)
        if not generator:
            raise ValueError(f"Unknown resource type: {resource_type}")

        manifest = generator(name, spec)

        # Inject anomaly if configured
        manifest = self.anomaly_injector.inject(manifest, anomaly)

        return manifest

    def _generate_deployment(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a Deployment manifest."""
        labels = spec.get("labels", {"app": name})
        selector_labels = spec.get("selector", {}).get("matchLabels", labels)

        container_spec = {
            "name": name,
            "image": spec.get("image", "nginx:latest"),
            "ports": [{"containerPort": p.get("containerPort", p) if isinstance(p, dict) else p}
                      for p in spec.get("ports", [])],
        }

        # Add resources if specified
        if spec.get("resources"):
            container_spec["resources"] = spec["resources"]

        # Add probes if specified
        if spec.get("readinessProbe"):
            container_spec["readinessProbe"] = spec["readinessProbe"]
        if spec.get("livenessProbe"):
            container_spec["livenessProbe"] = spec["livenessProbe"]

        # Add environment variables
        if spec.get("env"):
            container_spec["env"] = spec["env"]

        # Add volume mounts
        if spec.get("volumeMounts"):
            container_spec["volumeMounts"] = spec["volumeMounts"]

        pod_spec = {
            "containers": [container_spec],
        }

        # Add volumes
        if spec.get("volumes"):
            pod_spec["volumes"] = spec["volumes"]

        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": name,
                "namespace": self.namespace,
                "labels": {**labels, "managed-by": "chaosprobe"},
            },
            "spec": {
                "replicas": spec.get("replicas", 1),
                "selector": {"matchLabels": selector_labels},
                "template": {
                    "metadata": {"labels": selector_labels},
                    "spec": pod_spec,
                },
            },
        }

    def _generate_service(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a Service manifest."""
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": name,
                "namespace": self.namespace,
                "labels": {"managed-by": "chaosprobe"},
            },
            "spec": {
                "selector": spec.get("selector", {"app": name.replace("-service", "")}),
                "ports": spec.get("ports", [{"port": 80, "targetPort": 80}]),
                "type": spec.get("type", "ClusterIP"),
            },
        }

    def _generate_configmap(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a ConfigMap manifest."""
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": name,
                "namespace": self.namespace,
                "labels": {"managed-by": "chaosprobe"},
            },
            "data": spec.get("data", {}),
        }

    def _generate_secret(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a Secret manifest."""
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": name,
                "namespace": self.namespace,
                "labels": {"managed-by": "chaosprobe"},
            },
            "type": spec.get("type", "Opaque"),
            "data": spec.get("data", {}),
        }

    def _generate_pdb(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a PodDisruptionBudget manifest."""
        pdb_spec = {
            "selector": {"matchLabels": spec.get("selector", {})},
        }

        if "minAvailable" in spec:
            pdb_spec["minAvailable"] = spec["minAvailable"]
        elif "maxUnavailable" in spec:
            pdb_spec["maxUnavailable"] = spec["maxUnavailable"]
        else:
            pdb_spec["minAvailable"] = 1

        return {
            "apiVersion": "policy/v1",
            "kind": "PodDisruptionBudget",
            "metadata": {
                "name": name,
                "namespace": self.namespace,
                "labels": {"managed-by": "chaosprobe"},
            },
            "spec": pdb_spec,
        }

    def _generate_network_policy(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a NetworkPolicy manifest."""
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": name,
                "namespace": self.namespace,
                "labels": {"managed-by": "chaosprobe"},
            },
            "spec": {
                "podSelector": spec.get("podSelector", {}),
                "policyTypes": spec.get("policyTypes", ["Ingress", "Egress"]),
                "ingress": spec.get("ingress", []),
                "egress": spec.get("egress", []),
            },
        }

    def _generate_pvc(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a PersistentVolumeClaim manifest."""
        return {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": name,
                "namespace": self.namespace,
                "labels": {"managed-by": "chaosprobe"},
            },
            "spec": {
                "accessModes": spec.get("accessModes", ["ReadWriteOnce"]),
                "resources": {
                    "requests": {
                        "storage": spec.get("storage", "1Gi"),
                    },
                },
                "storageClassName": spec.get("storageClassName"),
            },
        }

    def _generate_hpa(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a HorizontalPodAutoscaler manifest."""
        return {
            "apiVersion": "autoscaling/v1",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {
                "name": name,
                "namespace": self.namespace,
                "labels": {"managed-by": "chaosprobe"},
            },
            "spec": {
                "scaleTargetRef": spec.get("scaleTargetRef", {}),
                "minReplicas": spec.get("minReplicas", 1),
                "maxReplicas": spec.get("maxReplicas", 10),
                "targetCPUUtilizationPercentage": spec.get("targetCPUUtilizationPercentage", 80),
            },
        }

    def _generate_ingress(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Generate an Ingress manifest."""
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": name,
                "namespace": self.namespace,
                "labels": {"managed-by": "chaosprobe"},
                "annotations": spec.get("annotations", {}),
            },
            "spec": {
                "ingressClassName": spec.get("ingressClassName"),
                "rules": spec.get("rules", []),
                "tls": spec.get("tls", []),
            },
        }

    def _apply_manifest(self, manifest: Dict[str, Any]):
        """Apply a manifest to the cluster."""
        kind = manifest["kind"]
        name = manifest["metadata"]["name"]
        namespace = manifest["metadata"].get("namespace", self.namespace)

        try:
            if kind == "Deployment":
                self.apps_api.create_namespaced_deployment(namespace, manifest)
            elif kind == "Service":
                self.core_api.create_namespaced_service(namespace, manifest)
            elif kind == "ConfigMap":
                self.core_api.create_namespaced_config_map(namespace, manifest)
            elif kind == "Secret":
                self.core_api.create_namespaced_secret(namespace, manifest)
            elif kind == "PodDisruptionBudget":
                self.policy_api.create_namespaced_pod_disruption_budget(namespace, manifest)
            elif kind == "NetworkPolicy":
                self.networking_api.create_namespaced_network_policy(namespace, manifest)
            elif kind == "PersistentVolumeClaim":
                self.core_api.create_namespaced_persistent_volume_claim(namespace, manifest)
            elif kind == "HorizontalPodAutoscaler":
                self.autoscaling_api.create_namespaced_horizontal_pod_autoscaler(namespace, manifest)
            elif kind == "Ingress":
                self.networking_api.create_namespaced_ingress(namespace, manifest)
            else:
                raise ValueError(f"Unknown kind: {kind}")

            self._provisioned_resources.append({
                "kind": kind,
                "name": name,
                "namespace": namespace,
            })

        except ApiException as e:
            if e.status == 409:
                # Resource already exists, update it
                self._update_manifest(manifest)
            else:
                raise

    def _update_manifest(self, manifest: Dict[str, Any]):
        """Update an existing manifest in the cluster."""
        kind = manifest["kind"]
        name = manifest["metadata"]["name"]
        namespace = manifest["metadata"].get("namespace", self.namespace)

        if kind == "Deployment":
            self.apps_api.replace_namespaced_deployment(name, namespace, manifest)
        elif kind == "Service":
            # Services need special handling for clusterIP
            existing = self.core_api.read_namespaced_service(name, namespace)
            manifest["spec"]["clusterIP"] = existing.spec.cluster_ip
            self.core_api.replace_namespaced_service(name, namespace, manifest)
        elif kind == "ConfigMap":
            self.core_api.replace_namespaced_config_map(name, namespace, manifest)
        elif kind == "Secret":
            self.core_api.replace_namespaced_secret(name, namespace, manifest)

    def _delete_resource(self, resource: Dict[str, Any]):
        """Delete a resource from the cluster."""
        kind = resource["kind"]
        name = resource["name"]
        namespace = resource.get("namespace", self.namespace)

        try:
            if kind == "Deployment":
                self.apps_api.delete_namespaced_deployment(name, namespace)
            elif kind == "Service":
                self.core_api.delete_namespaced_service(name, namespace)
            elif kind == "ConfigMap":
                self.core_api.delete_namespaced_config_map(name, namespace)
            elif kind == "Secret":
                self.core_api.delete_namespaced_secret(name, namespace)
            elif kind == "PodDisruptionBudget":
                self.policy_api.delete_namespaced_pod_disruption_budget(name, namespace)
            elif kind == "NetworkPolicy":
                self.networking_api.delete_namespaced_network_policy(name, namespace)
            elif kind == "PersistentVolumeClaim":
                self.core_api.delete_namespaced_persistent_volume_claim(name, namespace)
            elif kind == "HorizontalPodAutoscaler":
                self.autoscaling_api.delete_namespaced_horizontal_pod_autoscaler(name, namespace)
            elif kind == "Ingress":
                self.networking_api.delete_namespaced_ingress(name, namespace)
        except ApiException as e:
            if e.status != 404:
                raise

    def _wait_for_deployments(self, timeout: int = 120):
        """Wait for all deployments to be ready."""
        deployments = [r for r in self._provisioned_resources if r["kind"] == "Deployment"]

        start_time = time.time()
        for deployment in deployments:
            while time.time() - start_time < timeout:
                try:
                    d = self.apps_api.read_namespaced_deployment(
                        deployment["name"], deployment["namespace"]
                    )
                    if d.status.ready_replicas == d.spec.replicas:
                        break
                except ApiException:
                    pass
                time.sleep(2)

    def get_injected_anomalies(self) -> List[Dict[str, Any]]:
        """Get list of anomalies that were injected."""
        return self.anomaly_injector.get_injected_anomalies()
