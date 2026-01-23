"""Automatic setup and installation of LitmusChaos and dependencies."""

import subprocess
import time
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException


class LitmusSetup:
    """Handles automatic installation and verification of LitmusChaos."""

    LITMUS_NAMESPACE = "litmus"
    LITMUS_CRD_GROUP = "litmuschaos.io"

    def __init__(self):
        """Initialize the setup handler."""
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.core_api = client.CoreV1Api()
        self.apps_api = client.AppsV1Api()
        self.apiext_api = client.ApiextensionsV1Api()
        self.rbac_api = client.RbacAuthorizationV1Api()

    def is_litmus_installed(self) -> bool:
        """Check if LitmusChaos is installed in the cluster."""
        try:
            crds = self.apiext_api.list_custom_resource_definition()
            litmus_crds = [
                crd for crd in crds.items
                if crd.metadata.name.endswith(".litmuschaos.io")
            ]
            return len(litmus_crds) > 0
        except ApiException:
            return False

    def is_litmus_ready(self) -> bool:
        """Check if LitmusChaos is ready and running."""
        if not self.is_litmus_installed():
            return False

        try:
            ns = self.core_api.read_namespace(self.LITMUS_NAMESPACE)
            if ns.status.phase != "Active":
                return False

            deployments = self.apps_api.list_namespaced_deployment(self.LITMUS_NAMESPACE)
            for dep in deployments.items:
                if dep.status.ready_replicas != dep.spec.replicas:
                    return False

            return True
        except ApiException:
            return False

    def install_litmus(self, wait: bool = True, timeout: int = 180) -> bool:
        """Install LitmusChaos using Helm.

        Args:
            wait: Whether to wait for installation to complete.
            timeout: Timeout in seconds.

        Returns:
            True if installation succeeded.
        """
        self._ensure_namespace(self.LITMUS_NAMESPACE)

        try:
            subprocess.run(
                ["helm", "repo", "add", "litmuschaos",
                 "https://litmuschaos.github.io/litmus-helm/"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass

        try:
            subprocess.run(
                ["helm", "repo", "update"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to update helm repos: {e.stderr.decode()}")

        try:
            subprocess.run(
                [
                    "helm", "upgrade", "--install", "chaos",
                    "litmuschaos/litmus",
                    "--namespace", self.LITMUS_NAMESPACE,
                    "--set", "portalScope.scope=cluster",
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to install LitmusChaos: {e.stderr.decode()}")

        if wait:
            return self._wait_for_litmus(timeout)

        return True

    def setup_rbac(self, namespace: str) -> bool:
        """Setup RBAC for running chaos experiments in a namespace.

        Args:
            namespace: Target namespace for chaos experiments.

        Returns:
            True if RBAC setup succeeded.
        """
        self._ensure_namespace(namespace)

        sa = client.V1ServiceAccount(
            metadata=client.V1ObjectMeta(
                name="litmus-admin",
                namespace=namespace,
                labels={"managed-by": "chaosprobe"},
            )
        )

        try:
            self.core_api.create_namespaced_service_account(namespace, sa)
        except ApiException as e:
            if e.status != 409:
                raise

        cluster_role = client.V1ClusterRole(
            metadata=client.V1ObjectMeta(
                name=f"litmus-admin-{namespace}",
                labels={"managed-by": "chaosprobe"},
            ),
            rules=[
                client.V1PolicyRule(
                    api_groups=[""],
                    resources=["pods", "pods/log", "pods/exec", "events", "services",
                              "configmaps", "secrets", "persistentvolumeclaims", "nodes"],
                    verbs=["get", "list", "watch", "create", "update", "patch", "delete"],
                ),
                client.V1PolicyRule(
                    api_groups=["apps"],
                    resources=["deployments", "statefulsets", "replicasets", "daemonsets"],
                    verbs=["get", "list", "watch", "create", "update", "patch", "delete"],
                ),
                client.V1PolicyRule(
                    api_groups=["batch"],
                    resources=["jobs", "cronjobs"],
                    verbs=["get", "list", "watch", "create", "update", "patch", "delete"],
                ),
                client.V1PolicyRule(
                    api_groups=["litmuschaos.io"],
                    resources=["*"],
                    verbs=["*"],
                ),
            ],
        )

        try:
            self.rbac_api.create_cluster_role(cluster_role)
        except ApiException as e:
            if e.status == 409:
                self.rbac_api.replace_cluster_role(
                    f"litmus-admin-{namespace}", cluster_role
                )
            else:
                raise

        cluster_role_binding = client.V1ClusterRoleBinding(
            metadata=client.V1ObjectMeta(
                name=f"litmus-admin-{namespace}-binding",
                labels={"managed-by": "chaosprobe"},
            ),
            subjects=[
                client.V1Subject(
                    kind="ServiceAccount",
                    name="litmus-admin",
                    namespace=namespace,
                )
            ],
            role_ref=client.V1RoleRef(
                api_group="rbac.authorization.k8s.io",
                kind="ClusterRole",
                name=f"litmus-admin-{namespace}",
            ),
        )

        try:
            self.rbac_api.create_cluster_role_binding(cluster_role_binding)
        except ApiException as e:
            if e.status == 409:
                self.rbac_api.replace_cluster_role_binding(
                    f"litmus-admin-{namespace}-binding", cluster_role_binding
                )
            else:
                raise

        return True

    def install_experiment(self, experiment_type: str, namespace: str) -> bool:
        """Install a specific chaos experiment type.

        Args:
            experiment_type: The type of experiment (e.g., 'pod-delete').
            namespace: Target namespace.

        Returns:
            True if installation succeeded.
        """
        experiment_urls = {
            "pod-delete": "https://hub.litmuschaos.io/api/chaos/3.0.0?file=charts/generic/pod-delete/experiment.yaml",
            "container-kill": "https://hub.litmuschaos.io/api/chaos/3.0.0?file=charts/generic/container-kill/experiment.yaml",
            "pod-cpu-hog": "https://hub.litmuschaos.io/api/chaos/3.0.0?file=charts/generic/pod-cpu-hog/experiment.yaml",
            "pod-memory-hog": "https://hub.litmuschaos.io/api/chaos/3.0.0?file=charts/generic/pod-memory-hog/experiment.yaml",
            "pod-network-loss": "https://hub.litmuschaos.io/api/chaos/3.0.0?file=charts/generic/pod-network-loss/experiment.yaml",
            "pod-network-latency": "https://hub.litmuschaos.io/api/chaos/3.0.0?file=charts/generic/pod-network-latency/experiment.yaml",
            "pod-io-stress": "https://hub.litmuschaos.io/api/chaos/3.0.0?file=charts/generic/pod-io-stress/experiment.yaml",
            "node-drain": "https://hub.litmuschaos.io/api/chaos/3.0.0?file=charts/generic/node-drain/experiment.yaml",
            "node-cpu-hog": "https://hub.litmuschaos.io/api/chaos/3.0.0?file=charts/generic/node-cpu-hog/experiment.yaml",
            "node-memory-hog": "https://hub.litmuschaos.io/api/chaos/3.0.0?file=charts/generic/node-memory-hog/experiment.yaml",
        }

        url = experiment_urls.get(experiment_type)
        if not url:
            return False

        try:
            subprocess.run(
                ["kubectl", "apply", "-f", url, "-n", namespace],
                check=True,
                capture_output=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def full_setup(self, namespace: str, experiments: Optional[list] = None) -> bool:
        """Perform full setup: install Litmus, RBAC, and experiments.

        Args:
            namespace: Target namespace for chaos experiments.
            experiments: List of experiment types to install.

        Returns:
            True if all setup succeeded.
        """
        if not self.is_litmus_installed():
            self.install_litmus(wait=True)

        self.setup_rbac(namespace)

        if experiments:
            for exp_type in experiments:
                self.install_experiment(exp_type, namespace)

        return True

    def _ensure_namespace(self, namespace: str):
        """Create namespace if it doesn't exist."""
        try:
            self.core_api.read_namespace(namespace)
        except ApiException as e:
            if e.status == 404:
                ns = client.V1Namespace(
                    metadata=client.V1ObjectMeta(name=namespace)
                )
                self.core_api.create_namespace(ns)
            else:
                raise

    def _wait_for_litmus(self, timeout: int) -> bool:
        """Wait for LitmusChaos to be ready."""
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self.is_litmus_ready():
                return True
            time.sleep(5)

        return False

    def check_prerequisites(self) -> dict:
        """Check all prerequisites and return status.

        Returns:
            Dictionary with status of each prerequisite.
        """
        results = {
            "kubectl": self._check_kubectl(),
            "helm": self._check_helm(),
            "cluster_access": self._check_cluster_access(),
            "litmus_installed": self.is_litmus_installed(),
            "litmus_ready": self.is_litmus_ready(),
        }
        results["all_ready"] = all(results.values())
        return results

    def _check_kubectl(self) -> bool:
        """Check if kubectl is available."""
        try:
            subprocess.run(
                ["kubectl", "version", "--client"],
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _check_helm(self) -> bool:
        """Check if helm is available."""
        try:
            subprocess.run(
                ["helm", "version"],
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _check_cluster_access(self) -> bool:
        """Check if we have cluster access."""
        try:
            self.core_api.list_namespace()
            return True
        except ApiException:
            return False
