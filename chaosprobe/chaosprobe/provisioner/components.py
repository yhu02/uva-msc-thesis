"""Infrastructure component install methods for LitmusSetup (mixin).

Covers metrics-server, Prometheus, Neo4j, and local-path-provisioner.
"""

import os
import subprocess
import tempfile
import time
from typing import Any, Optional

from kubernetes import client
from kubernetes.client.rest import ApiException

from chaosprobe.probes.builder import DEFAULT_REGISTRY
from chaosprobe.provisioner._setup_base import _LitmusSetupBase

# In-cluster container registry for ChaosProbe probe images. Runs registry:2 on
# the control plane and is reachable at <control-plane-node-ip>:REGISTRY_NODEPORT
# from both the build host (docker push) and every node (containerd pull).
REGISTRY_NAMESPACE = "registry"
REGISTRY_NODEPORT = 30500

REGISTRY_MANIFEST = f"""\
---
apiVersion: v1
kind: Namespace
metadata:
  name: {REGISTRY_NAMESPACE}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: registry-data
  namespace: {REGISTRY_NAMESPACE}
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: local-path
  resources:
    requests:
      storage: 10Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: registry
  namespace: {REGISTRY_NAMESPACE}
  labels:
    app: registry
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: registry
  template:
    metadata:
      labels:
        app: registry
    spec:
      nodeSelector:
        node-role.kubernetes.io/control-plane: ""
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
        - key: node-role.kubernetes.io/master
          operator: Exists
          effect: NoSchedule
      containers:
        - name: registry
          image: registry:2
          ports:
            - containerPort: 5000
          env:
            - name: REGISTRY_STORAGE_DELETE_ENABLED
              value: "true"
            - name: REGISTRY_HTTP_ADDR
              value: ":5000"
          volumeMounts:
            - name: data
              mountPath: /var/lib/registry
          readinessProbe:
            httpGet:
              path: /v2/
              port: 5000
            initialDelaySeconds: 3
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /v2/
              port: 5000
            initialDelaySeconds: 10
            periodSeconds: 30
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 500m
              memory: 256Mi
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: registry-data
---
apiVersion: v1
kind: Service
metadata:
  name: registry
  namespace: {REGISTRY_NAMESPACE}
spec:
  type: NodePort
  selector:
    app: registry
  ports:
    - name: registry
      port: 5000
      targetPort: 5000
      nodePort: {REGISTRY_NODEPORT}
"""


def get_registry_address(core_api: Any) -> Optional[str]:
    """Return the in-cluster registry address ``<node-ip>:<nodePort>``, or None.

    Resolves the node the registry pod runs on and that node's InternalIP, so
    the build host and the cluster nodes reach the registry at the same address.
    ``core_api`` is the opaque (untyped) kubernetes CoreV1Api client.
    """
    try:
        svc = core_api.read_namespaced_service("registry", REGISTRY_NAMESPACE)
    except ApiException:
        return None
    node_port: Optional[int] = None
    for port in svc.spec.ports or []:
        if port.node_port:
            node_port = port.node_port
            break
    if node_port is None:
        return None
    try:
        pods = core_api.list_namespaced_pod(REGISTRY_NAMESPACE, label_selector="app=registry")
    except ApiException:
        return None
    node_name: Optional[str] = None
    for pod in pods.items:
        if pod.status.phase == "Running" and pod.spec.node_name:
            node_name = pod.spec.node_name
            break
    if node_name is None:
        return None
    try:
        node = core_api.read_node(node_name)
    except ApiException:
        return None
    for addr in node.status.addresses or []:
        if addr.type == "InternalIP":
            return f"{addr.address}:{node_port}"
    return None


def resolve_probe_registry(core_api: Any) -> str:
    """Pick the registry for probe images: explicit ``CHAOSPROBE_REGISTRY`` env
    override, else the in-cluster registry if installed, else the GHCR default."""
    return (
        os.environ.get("CHAOSPROBE_REGISTRY") or get_registry_address(core_api) or DEFAULT_REGISTRY
    )


class _ComponentsMixin(_LitmusSetupBase):
    """Metrics-server, Prometheus, Neo4j installation methods."""

    # -- metrics-server -----------------------------------------------------

    def is_metrics_server_installed(self) -> bool:
        """Check if metrics-server is available in the cluster."""
        if not self._k8s_initialized:
            return False
        try:
            custom = client.CustomObjectsApi()
            custom.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
            )
            return True
        except ApiException:
            return False

    def install_metrics_server(self, wait: bool = True, timeout: int = 120) -> bool:
        """Install metrics-server from the official manifest.

        Uses the high-availability manifest with ``--kubelet-insecure-tls``
        added for Vagrant/Kubespray clusters that use self-signed certs.
        """
        manifest_url = (
            "https://github.com/kubernetes-sigs/metrics-server"
            "/releases/latest/download/components.yaml"
        )
        print("Installing metrics-server...")
        try:
            subprocess.run(
                ["kubectl", "apply", "-f", manifest_url],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to apply metrics-server manifest: {e}") from e

        # Patch to add --kubelet-insecure-tls for self-signed certs
        patch = (
            '{"spec":{"template":{"spec":{'
            '"tolerations":[{"key":"node-role.kubernetes.io/control-plane",'
            '"operator":"Exists","effect":"NoSchedule"}],'
            '"nodeSelector":{"node-role.kubernetes.io/control-plane":""},'
            '"containers":[{'
            '"name":"metrics-server",'
            '"args":["--cert-dir=/tmp","--secure-port=10250",'
            '"--kubelet-preferred-address-types='
            'InternalIP,ExternalIP,Hostname",'
            '"--kubelet-use-node-status-port",'
            '"--metric-resolution=15s",'
            '"--kubelet-insecure-tls"]}]}}}}'
        )
        try:
            subprocess.run(
                [
                    "kubectl",
                    "patch",
                    "deployment",
                    "metrics-server",
                    "-n",
                    "kube-system",
                    "--type=strategic",
                    f"-p={patch}",
                ],
                check=True,
            )
        except subprocess.CalledProcessError:
            pass  # Patch may fail if args already set — non-fatal

        if wait:
            return self._wait_for_metrics_server(timeout)
        return True

    def _wait_for_metrics_server(self, timeout: int) -> bool:
        """Wait for metrics-server to become operational."""
        start = time.time()
        while time.time() - start < timeout:
            if self.is_metrics_server_installed():
                return True
            time.sleep(5)
        return False

    # -- Prometheus ---------------------------------------------------------

    def is_prometheus_installed(self) -> bool:
        """Check if Prometheus is running in the cluster."""
        if not self._k8s_initialized:
            return False
        try:
            services = self.core_api.list_namespaced_service("prometheus")
            for svc in services.items:
                if svc.metadata.name == "prometheus-server":
                    return True
        except ApiException:
            pass
        return False

    def install_prometheus(self, wait: bool = True, timeout: int = 180) -> bool:
        """Install Prometheus using the prometheus-community Helm chart."""
        self._ensure_namespace("prometheus")

        print("Adding prometheus-community Helm repository...")
        try:
            subprocess.run(
                [
                    "helm",
                    "repo",
                    "add",
                    "prometheus-community",
                    "https://prometheus-community.github.io/helm-charts",
                ],
                check=True,
            )
        except subprocess.CalledProcessError:
            pass  # Repo may already exist

        subprocess.run(["helm", "repo", "update"], check=True, capture_output=True)

        print("Installing Prometheus...")
        try:
            subprocess.run(
                [
                    "helm",
                    "upgrade",
                    "--install",
                    "prometheus",
                    "prometheus-community/prometheus",
                    "--namespace",
                    "prometheus",
                    "--set",
                    "alertmanager.enabled=false",
                    "--set",
                    "kube-state-metrics.enabled=true",
                    "--set",
                    "prometheus-pushgateway.enabled=false",
                    "--set",
                    "server.persistentVolume.enabled=true",
                    "--set",
                    "server.persistentVolume.size=2Gi",
                    "--set",
                    "server.retention=3d",
                    "--set",
                    "server.global.scrape_interval=15s",
                    "--set",
                    "server.global.evaluation_interval=15s",
                    "--set",
                    "server.tolerations[0].key=node-role.kubernetes.io/control-plane",
                    "--set",
                    "server.tolerations[0].operator=Exists",
                    "--set",
                    "server.tolerations[0].effect=NoSchedule",
                    "--set",
                    "server.nodeSelector.node-role\\.kubernetes\\.io/control-plane=",
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to install Prometheus: {e}") from e

        if wait:
            return self._wait_for_prometheus(timeout)
        return True

    def _wait_for_prometheus(self, timeout: int) -> bool:
        """Wait for Prometheus server to become ready."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                pods = self.core_api.list_namespaced_pod(
                    "prometheus",
                    label_selector="app.kubernetes.io/name=prometheus",
                )
                for pod in pods.items:
                    if pod.status.phase == "Running":
                        ready = all(cs.ready for cs in (pod.status.container_statuses or []))
                        if ready:
                            return True
            except ApiException:
                pass
            time.sleep(5)
        return False

    # -- Neo4j + storage ----------------------------------------------------

    def is_neo4j_installed(self) -> bool:
        """Check if Neo4j is running in the cluster."""
        if not self._k8s_initialized:
            return False
        try:
            services = self.core_api.list_namespaced_service("neo4j")
            for svc in services.items:
                if svc.metadata.name in ("neo4j", "neo4j-lb"):
                    return True
        except ApiException:
            pass
        return False

    def _ensure_storage_class(self) -> None:
        """Install local-path-provisioner if no StorageClass exists."""
        try:
            sc_list = self.storage_api.list_storage_class()
            if sc_list.items:
                return
        except Exception:
            pass

        self._install_local_path_provisioner()

    def is_local_path_provisioner_running(self) -> bool:
        """Check if the local-path-provisioner pod is running."""
        try:
            pods = self.core_api.list_namespaced_pod(
                namespace="local-path-storage",
                label_selector="app=local-path-provisioner",
            )
            for pod in pods.items:
                if pod.status.phase == "Running":
                    return True
        except Exception:
            pass
        return False

    def ensure_local_path_provisioner(self) -> bool:
        """Ensure local-path-provisioner is installed and running.

        Returns:
            True if provisioner is running after the check.
        """
        if self.is_local_path_provisioner_running():
            return True
        self._install_local_path_provisioner()
        for _ in range(15):
            time.sleep(2)
            if self.is_local_path_provisioner_running():
                return True
        return False

    def _install_local_path_provisioner(self) -> None:
        """Apply the local-path-provisioner manifest."""
        print("Installing local-path-provisioner...")
        subprocess.run(
            [
                "kubectl",
                "apply",
                "-f",
                "https://raw.githubusercontent.com/rancher/local-path-provisioner/"
                "v0.0.26/deploy/local-path-storage.yaml",
            ],
            check=True,
        )
        # Mark as default StorageClass
        subprocess.run(
            [
                "kubectl",
                "patch",
                "storageclass",
                "local-path",
                "-p",
                '{"metadata":{"annotations":'
                '{"storageclass.kubernetes.io/is-default-class":"true"}}}',
            ],
            check=True,
        )
        print("  local-path-provisioner installed")

    def is_registry_installed(self) -> bool:
        """Return True if the in-cluster registry Deployment exists."""
        try:
            self.apps_api.read_namespaced_deployment("registry", REGISTRY_NAMESPACE)
            return True
        except ApiException:
            return False

    def install_registry(self, wait: bool = True, timeout: int = 180) -> bool:
        """Deploy the in-cluster container registry (registry:2) on the control plane.

        Idempotent — applies the manifest and (optionally) waits for the pod to be
        ready. Nodes and the build host must still be configured to trust it as an
        insecure registry (containerd / docker); see ``manifests/README.md``.
        """
        print("Installing in-cluster registry (registry:2)...")
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write(REGISTRY_MANIFEST)
            manifest_path = handle.name
        try:
            subprocess.run(["kubectl", "apply", "-f", manifest_path], check=True)
        finally:
            os.unlink(manifest_path)
        if not wait:
            return True
        return self._wait_for_registry(timeout)

    def _wait_for_registry(self, timeout: int) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            try:
                pods = self.core_api.list_namespaced_pod(
                    REGISTRY_NAMESPACE, label_selector="app=registry"
                )
                for pod in pods.items:
                    if pod.status.phase == "Running" and all(
                        cs.ready for cs in (pod.status.container_statuses or [])
                    ):
                        return True
            except ApiException:
                pass
            time.sleep(5)
        return False

    def install_neo4j(self, wait: bool = True, timeout: int = 300) -> bool:
        """Install Neo4j as a lightweight Deployment.

        Returns:
            True if installation succeeded.
        """
        self._ensure_namespace("neo4j")
        self._ensure_storage_class()

        pvc_manifest = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "neo4j-data", "namespace": "neo4j"},
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "1Gi"}},
            },
        }

        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "neo4j", "namespace": "neo4j"},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": "neo4j"}},
                "template": {
                    "metadata": {"labels": {"app": "neo4j"}},
                    "spec": {
                        "tolerations": [
                            {
                                "key": "node-role.kubernetes.io/control-plane",
                                "operator": "Exists",
                                "effect": "NoSchedule",
                            }
                        ],
                        "nodeSelector": {
                            "node-role.kubernetes.io/control-plane": "",
                        },
                        "containers": [
                            {
                                "name": "neo4j",
                                "image": "neo4j:5-community",
                                "env": [
                                    {"name": "NEO4J_AUTH", "value": "neo4j/chaosprobe"},
                                    {
                                        "name": "NEO4J_server_memory_heap_initial__size",
                                        "value": "256m",
                                    },
                                    {"name": "NEO4J_server_memory_heap_max__size", "value": "256m"},
                                    {"name": "NEO4J_server_memory_pagecache_size", "value": "64m"},
                                    {
                                        "name": "NEO4J_server_config_strict__validation_enabled",
                                        "value": "false",
                                    },
                                ],
                                "ports": [
                                    {"containerPort": 7474, "name": "http"},
                                    {"containerPort": 7687, "name": "bolt"},
                                ],
                                "resources": {
                                    "requests": {"cpu": "250m", "memory": "512Mi"},
                                    "limits": {"cpu": "500m", "memory": "768Mi"},
                                },
                                "readinessProbe": {
                                    "tcpSocket": {"port": 7687},
                                    "initialDelaySeconds": 30,
                                    "periodSeconds": 5,
                                    "failureThreshold": 12,
                                },
                                "volumeMounts": [
                                    {
                                        "name": "neo4j-data",
                                        "mountPath": "/data",
                                    }
                                ],
                            }
                        ],
                        "volumes": [
                            {
                                "name": "neo4j-data",
                                "persistentVolumeClaim": {"claimName": "neo4j-data"},
                            }
                        ],
                    },
                },
            },
        }

        svc_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "neo4j", "namespace": "neo4j"},
            "spec": {
                "selector": {"app": "neo4j"},
                "ports": [
                    {"name": "http", "port": 7474, "targetPort": 7474},
                    {"name": "bolt", "port": 7687, "targetPort": 7687},
                ],
            },
        }

        print("Installing Neo4j...")
        try:
            from kubernetes.utils import create_from_dict

            k8s_client = client.ApiClient()

            # Apply PVC (skip if already exists)
            try:
                self.core_api.read_namespaced_persistent_volume_claim("neo4j-data", "neo4j")
            except ApiException as e:
                if e.status == 404:
                    create_from_dict(k8s_client, pvc_manifest)
                else:
                    raise

            # Apply deployment
            try:
                self.apps_api.read_namespaced_deployment("neo4j", "neo4j")
                self.apps_api.patch_namespaced_deployment("neo4j", "neo4j", manifest)
            except ApiException as e:
                if e.status == 404:
                    create_from_dict(k8s_client, manifest)
                else:
                    raise

            # Apply service
            try:
                self.core_api.read_namespaced_service("neo4j", "neo4j")
            except ApiException as e:
                if e.status == 404:
                    create_from_dict(k8s_client, svc_manifest)
                else:
                    raise
        except Exception as e:
            raise RuntimeError(f"Failed to install Neo4j: {e}") from e

        if wait:
            return self._wait_for_neo4j(timeout)
        return True

    def _wait_for_neo4j(self, timeout: int) -> bool:
        """Wait for Neo4j to become ready."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                pods = self.core_api.list_namespaced_pod(
                    "neo4j",
                    label_selector="app=neo4j",
                )
                for pod in pods.items:
                    if pod.status.phase == "Running":
                        ready = all(cs.ready for cs in (pod.status.container_statuses or []))
                        if ready:
                            return True
            except ApiException:
                pass
            time.sleep(5)
        return False
