"""Kubernetes probe generator for LitmusChaos experiments."""

from typing import Any, Dict, List, Optional


class K8sProbeGenerator:
    """Generates Kubernetes probe configurations for LitmusChaos."""

    @staticmethod
    def generate(
        name: str,
        group: str,
        version: str,
        resource: str,
        namespace: str,
        operation: str = "present",
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        mode: str = "Edge",
        timeout: str = "10s",
        interval: str = "5s",
        retry: int = 3,
    ) -> Dict[str, Any]:
        """Generate a Kubernetes probe configuration.

        Args:
            name: Probe name.
            group: API group (empty string for core resources).
            version: API version.
            resource: Resource type (e.g., "pods", "deployments").
            namespace: Target namespace.
            operation: Check operation (present, absent, create, delete).
            label_selector: Kubernetes label selector.
            field_selector: Kubernetes field selector.
            mode: Probe mode (SOT, EOT, Edge, Continuous, OnChaos).
            timeout: Probe timeout.
            interval: Probe interval.
            retry: Number of retries.

        Returns:
            Probe configuration dictionary.
        """
        probe = {
            "name": name,
            "type": "k8sProbe",
            "mode": mode,
            "runProperties": {
                "probeTimeout": timeout,
                "interval": interval,
                "retry": retry,
                "probePollingInterval": "1s",
            },
            "k8sProbe": {
                "group": group,
                "version": version,
                "resource": resource,
                "namespace": namespace,
                "operation": operation,
            },
        }

        if label_selector:
            probe["k8sProbe"]["labelSelector"] = label_selector

        if field_selector:
            probe["k8sProbe"]["fieldSelector"] = field_selector

        return probe

    @staticmethod
    def generate_pod_present(
        name: str,
        namespace: str,
        label_selector: str,
        mode: str = "Continuous",
    ) -> Dict[str, Any]:
        """Generate a probe to check if pods are present.

        Args:
            name: Probe name.
            namespace: Target namespace.
            label_selector: Label selector for pods.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        return K8sProbeGenerator.generate(
            name=name,
            group="",
            version="v1",
            resource="pods",
            namespace=namespace,
            operation="present",
            label_selector=label_selector,
            mode=mode,
        )

    @staticmethod
    def generate_deployment_ready(
        name: str,
        namespace: str,
        deployment_name: str,
        mode: str = "Edge",
    ) -> Dict[str, Any]:
        """Generate a probe to check if deployment is ready.

        Args:
            name: Probe name.
            namespace: Target namespace.
            deployment_name: Name of the deployment.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        return K8sProbeGenerator.generate(
            name=name,
            group="apps",
            version="v1",
            resource="deployments",
            namespace=namespace,
            operation="present",
            field_selector=f"metadata.name={deployment_name}",
            mode=mode,
        )

    @staticmethod
    def generate_service_endpoints_present(
        name: str,
        namespace: str,
        service_name: str,
        mode: str = "Continuous",
    ) -> Dict[str, Any]:
        """Generate a probe to check if service has endpoints.

        Args:
            name: Probe name.
            namespace: Target namespace.
            service_name: Name of the service.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        return K8sProbeGenerator.generate(
            name=name,
            group="",
            version="v1",
            resource="endpoints",
            namespace=namespace,
            operation="present",
            field_selector=f"metadata.name={service_name}",
            mode=mode,
        )

    @staticmethod
    def generate_pvc_bound(
        name: str,
        namespace: str,
        pvc_name: str,
        mode: str = "Edge",
    ) -> Dict[str, Any]:
        """Generate a probe to check if PVC is bound.

        Args:
            name: Probe name.
            namespace: Target namespace.
            pvc_name: Name of the PVC.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        return K8sProbeGenerator.generate(
            name=name,
            group="",
            version="v1",
            resource="persistentvolumeclaims",
            namespace=namespace,
            operation="present",
            field_selector=f"metadata.name={pvc_name},status.phase=Bound",
            mode=mode,
        )

    @staticmethod
    def generate_node_ready(
        name: str,
        node_name: str,
        mode: str = "Continuous",
    ) -> Dict[str, Any]:
        """Generate a probe to check if node is ready.

        Args:
            name: Probe name.
            node_name: Name of the node.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        return K8sProbeGenerator.generate(
            name=name,
            group="",
            version="v1",
            resource="nodes",
            namespace="",  # Nodes are cluster-scoped
            operation="present",
            field_selector=f"metadata.name={node_name}",
            mode=mode,
        )

    @staticmethod
    def generate_resource_absent(
        name: str,
        namespace: str,
        resource: str,
        label_selector: str,
        mode: str = "Edge",
    ) -> Dict[str, Any]:
        """Generate a probe to check if resources are absent.

        Args:
            name: Probe name.
            namespace: Target namespace.
            resource: Resource type (e.g., "pods").
            label_selector: Label selector.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        group = ""
        version = "v1"

        # Handle common resource types
        if resource in ["deployments", "replicasets", "statefulsets", "daemonsets"]:
            group = "apps"

        return K8sProbeGenerator.generate(
            name=name,
            group=group,
            version=version,
            resource=resource,
            namespace=namespace,
            operation="absent",
            label_selector=label_selector,
            mode=mode,
        )
