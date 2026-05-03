"""ChaosCenter installation, status, and configuration mixin for LitmusSetup.

Covers installation (Helm), status checks, dashboard URL detection,
and the ``ensure_chaoscenter_configured`` orchestrator.

Low-level API / GraphQL helpers live in ``chaoscenter_api.py``.
"""

import subprocess
import time
from typing import Any, Optional

from kubernetes.client.rest import ApiException


class _ChaosCenterMixin:
    """ChaosCenter installation & configuration methods mixed into LitmusSetup."""

    def is_chaoscenter_installed(self) -> bool:
        """Check if ChaosCenter dashboard is installed."""
        if not self._k8s_initialized:
            return False
        try:
            svcs = self.core_api.list_namespaced_service(self.LITMUS_NAMESPACE)
            svc_names = {s.metadata.name for s in svcs.items}
            return self.CHAOSCENTER_FRONTEND_SVC in svc_names
        except Exception:
            return False

    def is_chaoscenter_ready(self) -> bool:
        """Check if ChaosCenter pods are running and ready."""
        if not self.is_chaoscenter_installed():
            return False
        try:
            deployments = self.apps_api.list_namespaced_deployment(self.LITMUS_NAMESPACE)
            required_fragments = ["frontend", "server", "auth"]
            for frag in required_fragments:
                found_ready = False
                for dep in deployments.items:
                    if frag in dep.metadata.name.lower():
                        if (
                            dep.status.ready_replicas is not None
                            and dep.status.ready_replicas == dep.spec.replicas
                        ):
                            found_ready = True
                            break
                if not found_ready:
                    return False
            return True
        except Exception:
            return False

    def install_chaoscenter(
        self,
        service_type: str = "NodePort",
        wait: bool = True,
        timeout: int = 300,
    ) -> bool:
        """Install ChaosCenter (full dashboard) using Helm.

        This installs the full ``litmuschaos/litmus`` chart which includes:
        frontend, GraphQL server, auth-server, MongoDB, subscriber,
        chaos-operator, chaos-exporter, and workflow-controller.

        Args:
            service_type: Kubernetes service type for frontend (NodePort or LoadBalancer).
            wait: Whether to wait for all pods to become ready.
            timeout: Timeout in seconds.

        Returns:
            True if installation succeeded.
        """
        self._ensure_namespace(self.LITMUS_NAMESPACE)

        # Ensure the helm repo is present
        try:
            subprocess.run(
                [
                    "helm",
                    "repo",
                    "add",
                    "litmuschaos",
                    "https://litmuschaos.github.io/litmus-helm/",
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # repo may already exist

        try:
            subprocess.run(["helm", "repo", "update"], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to update helm repos: {e}") from e

        print("Installing ChaosCenter dashboard...")
        try:
            subprocess.run(
                [
                    "helm",
                    "upgrade",
                    "--install",
                    self.CHAOSCENTER_RELEASE_NAME,
                    self.CHAOSCENTER_HELM_CHART,
                    "--namespace",
                    self.LITMUS_NAMESPACE,
                    "--set",
                    f"portal.frontend.service.type={service_type}",
                    "--set",
                    f"portal.server.service.type={service_type}",
                    # Pin all ChaosCenter components to control plane
                    "--set",
                    "portal.frontend.nodeSelector.node-role\\.kubernetes\\.io/control-plane=",
                    "--set",
                    "portal.frontend.tolerations[0].key=node-role.kubernetes.io/control-plane",
                    "--set",
                    "portal.frontend.tolerations[0].operator=Exists",
                    "--set",
                    "portal.frontend.tolerations[0].effect=NoSchedule",
                    "--set",
                    "portal.server.nodeSelector.node-role\\.kubernetes\\.io/control-plane=",
                    "--set",
                    "portal.server.tolerations[0].key=node-role.kubernetes.io/control-plane",
                    "--set",
                    "portal.server.tolerations[0].operator=Exists",
                    "--set",
                    "portal.server.tolerations[0].effect=NoSchedule",
                    "--set",
                    "mongodb.nodeSelector.node-role\\.kubernetes\\.io/control-plane=",
                    "--set",
                    "mongodb.tolerations[0].key=node-role.kubernetes.io/control-plane",
                    "--set",
                    "mongodb.tolerations[0].operator=Exists",
                    "--set",
                    "mongodb.tolerations[0].effect=NoSchedule",
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to install ChaosCenter: {e}") from e

        if wait:
            return self._wait_for_chaoscenter(timeout)
        return True

    def _wait_for_chaoscenter(self, timeout: int) -> bool:
        """Wait for ChaosCenter to become ready."""
        start = time.time()
        last_status = ""
        while time.time() - start < timeout:
            if self.is_chaoscenter_ready():
                print("  ChaosCenter: all pods ready")
                return True
            # Print progress so the user doesn't think we're stuck
            try:
                pods = self.core_api.list_namespaced_pod(self.LITMUS_NAMESPACE)
                statuses = []
                for pod in pods.items:
                    name = pod.metadata.name
                    phase = pod.status.phase or "Unknown"
                    cs = pod.status.container_statuses or []
                    if cs and all(c.ready for c in cs):
                        statuses.append(f"{name}=Ready")
                    else:
                        # Show init container status if stuck there
                        init_cs = pod.status.init_container_statuses or []
                        if init_cs and not all(c.ready for c in init_cs):
                            statuses.append(f"{name}=Init")
                        else:
                            statuses.append(f"{name}={phase}")
                status_line = ", ".join(sorted(statuses))
                elapsed = int(time.time() - start)
                msg = f"  Waiting for ChaosCenter pods ({elapsed}s): {status_line}"
                if msg != last_status:
                    print(msg)
                    last_status = msg
            except Exception:
                pass
            time.sleep(10)
        return False

    def get_chaoscenter_status(self) -> dict:
        """Return detailed status of the ChaosCenter deployment.

        Returns:
            Dictionary with keys: installed, ready, pods, frontend_url.
        """
        result: dict[str, Any] = {
            "installed": self.is_chaoscenter_installed(),
            "ready": False,
            "pods": [],
            "frontend_url": None,
        }
        if not result["installed"]:
            return result

        result["ready"] = self.is_chaoscenter_ready()

        try:
            pods = self.core_api.list_namespaced_pod(self.LITMUS_NAMESPACE)
            for pod in pods.items:
                containers = pod.status.container_statuses or []
                result["pods"].append(
                    {
                        "name": pod.metadata.name,
                        "phase": pod.status.phase,
                        "ready": all(c.ready for c in containers),
                    }
                )
        except Exception:
            pass

        result["frontend_url"] = self.get_dashboard_url()
        return result

    def get_dashboard_url(self) -> Optional[str]:
        """Detect and return the ChaosCenter frontend URL.

        Supports NodePort services (returns ``http://<node>:<nodePort>``)
        and LoadBalancer services.  Returns ``None`` when the URL cannot
        be determined.
        """
        if not self._k8s_initialized:
            return None
        try:
            svc = self.core_api.read_namespaced_service(
                self.CHAOSCENTER_FRONTEND_SVC,
                self.LITMUS_NAMESPACE,
            )
        except Exception:
            return None

        svc_type = svc.spec.type
        port_obj = svc.spec.ports[0] if svc.spec.ports else None
        if port_obj is None:
            return None

        if svc_type == "LoadBalancer":
            ingress = (svc.status.load_balancer or {}).ingress
            if ingress:
                host = ingress[0].ip or ingress[0].hostname
                return f"http://{host}:{port_obj.port}"
            return None

        if svc_type == "NodePort" and port_obj.node_port:
            node_ip = self._get_node_ip()
            if node_ip:
                return f"http://{node_ip}:{port_obj.node_port}"
            return None

        return None

    def _get_node_ip(self) -> Optional[str]:
        """Return the IP of the first schedulable node."""
        try:
            nodes = self.core_api.list_node()
            for node in nodes.items:
                for addr in node.status.addresses:
                    if addr.type == "InternalIP":
                        return addr.address
        except Exception:
            pass
        return None

    def _ensure_litmus_crds(self) -> bool:
        """Check and install CRDs required by LitmusChaos infrastructure.

        Returns True if any CRDs were installed (components may need restart).
        """
        from kubernetes import client as k8s_client_mod

        ext_api = k8s_client_mod.ApiextensionsV1Api()
        existing_crds = set()
        try:
            crd_list = ext_api.list_custom_resource_definition()
            existing_crds = {c.metadata.name for c in crd_list.items}
        except Exception:
            return False

        required_crds = {
            "workflows.argoproj.io": (
                "https://raw.githubusercontent.com/argoproj/argo-workflows/"
                "v3.3.1/manifests/base/crds/minimal/argoproj.io_workflows.yaml"
            ),
            "cronworkflows.argoproj.io": (
                "https://raw.githubusercontent.com/argoproj/argo-workflows/"
                "v3.3.1/manifests/base/crds/minimal/argoproj.io_cronworkflows.yaml"
            ),
            "workflowtemplates.argoproj.io": (
                "https://raw.githubusercontent.com/argoproj/argo-workflows/"
                "v3.3.1/manifests/base/crds/minimal/argoproj.io_workflowtemplates.yaml"
            ),
            "workflowtasksets.argoproj.io": (
                "https://raw.githubusercontent.com/argoproj/argo-workflows/"
                "v3.3.1/manifests/base/crds/minimal/argoproj.io_workflowtasksets.yaml"
            ),
            "workflowtaskresults.argoproj.io": (
                "https://raw.githubusercontent.com/argoproj/argo-workflows/"
                "v3.3.1/manifests/base/crds/minimal/argoproj.io_workflowtaskresults.yaml"
            ),
        }

        # EventTrackerPolicy CRD (inline — no upstream URL available)
        etp_crd_name = "eventtrackerpolicies.eventtracker.litmuschaos.io"

        installed_any = False
        for crd_name, url in required_crds.items():
            if crd_name not in existing_crds:
                print(f"  Installing missing CRD: {crd_name}")
                try:
                    subprocess.run(
                        ["kubectl", "apply", "-f", url],
                        check=True, capture_output=True,
                    )
                    installed_any = True
                except subprocess.CalledProcessError as e:
                    print(f"  WARNING: failed to install CRD {crd_name}: {e}")

        if etp_crd_name not in existing_crds:
            print(f"  Installing missing CRD: {etp_crd_name}")
            import tempfile, os
            etp_manifest = """\
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: eventtrackerpolicies.eventtracker.litmuschaos.io
spec:
  group: eventtracker.litmuschaos.io
  names:
    kind: EventTrackerPolicy
    listKind: EventTrackerPolicyList
    plural: eventtrackerpolicies
    singular: eventtrackerpolicy
  scope: Namespaced
  versions:
    - name: v1
      served: true
      storage: true
      schema:
        openAPIV3Schema:
          type: object
          x-kubernetes-preserve-unknown-fields: true
      subresources:
        status: {}
"""
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False,
            ) as f:
                f.write(etp_manifest)
                f.flush()
                try:
                    subprocess.run(
                        ["kubectl", "apply", "-f", f.name],
                        check=True, capture_output=True,
                    )
                    installed_any = True
                except subprocess.CalledProcessError as e:
                    print(f"  WARNING: failed to install CRD {etp_crd_name}: {e}")
                finally:
                    os.unlink(f.name)

        return installed_any

    def ensure_chaoscenter_configured(
        self,
        namespace: str,
        base_host: str = "http://localhost",
        username: str = "",
        password: str = "",
        timeout: int = 120,
    ) -> dict:
        """Idempotently configure ChaosCenter for *namespace*.

        1. Authenticate (auto-rotates default password).
        2. Create environment ``chaosprobe-<ns>`` if absent.
        3. Register infrastructure + apply subscriber if absent.
        4. Wait for subscriber pod to appear.

        Args:
            namespace: Target Kubernetes namespace.
            base_host: Scheme + host (no port), e.g. ``http://localhost``.
            username: ChaosCenter username.
            password: ChaosCenter password (optional — tries managed/default).
            timeout: Seconds to wait for subscriber readiness.

        Returns:
            Dict with ``token``, ``project_id``, ``environment_id``,
            ``infra_id`` keys.
        """
        auth_url = self._chaoscenter_auth_url(base_host)
        gql_url = self._chaoscenter_gql_url(base_host)

        # --- authenticate ------------------------------------------------
        token, project_id = self._chaoscenter_login(
            auth_url,
            username=username,
            password=password,
        )
        if not project_id:
            raise RuntimeError("ChaosCenter login did not return a projectID")

        env_name = f"chaosprobe-{namespace}"

        # --- environment -------------------------------------------------
        envs = self._chaoscenter_list_environments(gql_url, project_id, token)
        env_ids = {e["environmentID"] for e in envs}
        if env_name not in env_ids:
            self._chaoscenter_create_environment(gql_url, project_id, env_name, token)
            print(f"  ChaosCenter: created environment '{env_name}'")
        else:
            print(f"  ChaosCenter: environment '{env_name}' exists")

        # --- infrastructure ----------------------------------------------
        infras = self._chaoscenter_list_infras(gql_url, project_id, token)

        # Clean up infra components from OTHER namespaces to avoid
        # duplicate chaos-operator / subscriber / etc. hogging resources.
        other_infras = [
            i
            for i in infras
            if i.get("infraNamespace") != namespace
            and i.get("infraNamespace")  # skip entries without a namespace
        ]
        for other in other_infras:
            other_ns = other["infraNamespace"]
            infra_deployments = [
                "chaos-exporter",
                "chaos-operator-ce",
                "event-tracker",
                "subscriber",
                "workflow-controller",
            ]
            has_infra = False
            for dep_name in infra_deployments:
                try:
                    self.apps_api.read_namespaced_deployment(dep_name, other_ns)
                    has_infra = True
                    break
                except ApiException:
                    pass
            if has_infra:
                print(
                    f"  ChaosCenter: removing stale infra from '{other_ns}' "
                    f"(freeing resources for '{namespace}')"
                )
                for dep_name in infra_deployments:
                    try:
                        self.apps_api.delete_namespaced_deployment(
                            dep_name,
                            other_ns,
                        )
                    except ApiException:
                        pass

        existing = [
            i
            for i in infras
            if i.get("infraNamespace") == namespace and i.get("environmentID") == env_name
        ]

        if existing and existing[0].get("isActive"):
            infra_id = existing[0]["infraID"]
            print(f"  ChaosCenter: infrastructure already active ({infra_id})")
        elif existing:
            # Infra registered but subscriber not yet connected — don't
            # re-register (which would create a duplicate).  Just ensure
            # the subscriber deployment exists and wait for it.
            infra_id = existing[0]["infraID"]
            print(
                f"  ChaosCenter: infrastructure registered, "
                f"awaiting subscriber connection ({infra_id})"
            )
            # Ensure required CRDs exist — they may have been removed
            # (e.g. cluster rebuild) while the infra registration persists
            # in ChaosCenter's database.  Missing CRDs cause
            # workflow-controller and event-tracker to CrashLoopBackOff.
            if self._ensure_litmus_crds():
                print("  ChaosCenter: installed missing CRDs, restarting infra pods")
                for dep_name in ("workflow-controller", "event-tracker", "subscriber"):
                    try:
                        from datetime import datetime, timezone
                        self.apps_api.patch_namespaced_deployment(
                            dep_name, namespace, {
                                "spec": {"template": {"metadata": {"annotations": {
                                    "chaosprobe.io/crdRepair": datetime.now(
                                        timezone.utc
                                    ).isoformat(),
                                }}}},
                            },
                        )
                    except Exception:
                        pass
                # Brief pause for new pods to start
                time.sleep(10)
            # Ensure subscriber deployment exists — it may have been
            # evicted, deleted, or never applied (e.g. namespace was
            # recreated).  Always check regardless of isInfraConfirmed.
            subscriber_exists = False
            try:
                self.apps_api.read_namespaced_deployment(
                    "subscriber",
                    namespace,
                )
                subscriber_exists = True
            except ApiException as exc:
                if exc.status == 404:
                    subscriber_exists = False
                else:
                    # Transient API error — assume missing to be safe
                    subscriber_exists = False
            except Exception:
                subscriber_exists = False

            if not subscriber_exists:
                print("  ChaosCenter: subscriber deployment missing — re-applying")
                try:
                    manifest_resp = self._chaoscenter_api_request(
                        gql_url,
                        data={
                            "query": (
                                "query($pid: ID!, $iid: ID!, $upgrade: Boolean!) "
                                "{ getInfraManifest(projectID: $pid, "
                                "infraID: $iid, upgrade: $upgrade) }"
                            ),
                            "variables": {
                                "pid": project_id,
                                "iid": infra_id,
                                "upgrade": False,
                            },
                        },
                        token=token,
                        headers={
                            "Referer": self._chaoscenter_server_internal_url(),
                        },
                    )
                    manifest = manifest_resp.get("data", {}).get("getInfraManifest", "")
                    if manifest:
                        self._apply_manifest(manifest, namespace)
                    else:
                        print("  ChaosCenter: WARNING - empty manifest returned")
                except Exception as e:
                    print(f"  ChaosCenter: WARNING - could not re-apply manifest: {e}")

            # Wait for subscriber pod readiness
            start = time.time()
            while time.time() - start < timeout:
                try:
                    pods = self.core_api.list_namespaced_pod(
                        namespace,
                        label_selector="app=subscriber",
                    )
                    if pods.items and all(
                        c.ready for p in pods.items for c in (p.status.container_statuses or [])
                    ):
                        print("  ChaosCenter: subscriber pod ready")
                        break
                except Exception:
                    pass
                time.sleep(5)
            else:
                # Collect diagnostic info
                diag = self._subscriber_diagnostics(namespace)
                raise RuntimeError(f"Subscriber pod not ready after {timeout}s.\n{diag}")
        else:
            # No infra exists — register a new one
            self._ensure_litmus_crds()
            result = self._chaoscenter_register_infra(
                gql_url,
                project_id,
                env_name,
                namespace,
                token,
            )
            infra_id = result["infraID"]
            manifest = result.get("manifest", "")
            if manifest:
                self._apply_manifest(manifest, namespace)
                print(f"  ChaosCenter: subscriber manifest applied to '{namespace}'")

            # Wait for subscriber pod
            start = time.time()
            while time.time() - start < timeout:
                try:
                    pods = self.core_api.list_namespaced_pod(
                        namespace,
                        label_selector="app=subscriber",
                    )
                    if pods.items and all(
                        c.ready for p in pods.items for c in (p.status.container_statuses or [])
                    ):
                        print("  ChaosCenter: subscriber pod ready")
                        break
                except Exception:
                    pass
                time.sleep(5)
            else:
                diag = self._subscriber_diagnostics(namespace)
                raise RuntimeError(f"Subscriber pod not ready after {timeout}s.\n{diag}")

        # --- wait for infrastructure to become active --------------------
        # The subscriber pod can be Running+Ready before its WebSocket
        # to ChaosCenter is established.  Poll until isActive flips.
        if not (existing and existing[0].get("isActive")):
            print("  ChaosCenter: waiting for infrastructure to become active...")
            active_timeout = min(timeout, 120)
            if self._wait_for_infra_active(
                gql_url,
                project_id,
                token,
                infra_id,
                timeout=active_timeout,
            ):
                print(f"  ChaosCenter: infrastructure active ({infra_id})")
            else:
                raise RuntimeError(
                    f"Infrastructure {infra_id} did not become active "
                    f"after {active_timeout}s. The subscriber may have "
                    "failed to connect — check its logs:\n"
                    f"  kubectl logs -n {namespace} -l app=subscriber"
                )

        return {
            "token": token,
            "project_id": project_id,
            "environment_id": env_name,
            "infra_id": infra_id,
        }

    def connect_infrastructure(
        self,
        namespace: str,
        dashboard_url: Optional[str] = None,
        username: str = "",
        password: str = "",
    ) -> dict:
        """Register a namespace as chaos infrastructure in ChaosCenter.

        This authenticates to ChaosCenter, creates a new environment
        (if needed), and registers the namespace as a Kubernetes
        infrastructure via the GraphQL API.

        Args:
            namespace: The Kubernetes namespace to register.
            dashboard_url: Override auto-detected dashboard URL.
            username: ChaosCenter username (defaults to ``admin``).
            password: ChaosCenter password (defaults to ``litmus``).

        Returns:
            Dict with ``infra_id`` and ``manifest`` keys on success.
        """
        base_url = dashboard_url or self.get_dashboard_url()
        if not base_url:
            raise RuntimeError("Cannot detect ChaosCenter URL. Is it installed and ready?")

        # Derive scheme + host (strip port)
        base_host = base_url.rsplit(":", 1)[0]

        result = self.ensure_chaoscenter_configured(
            namespace=namespace,
            base_host=base_host,
            username=username,
            password=password,
        )
        return {"infra_id": result["infra_id"], "manifest": ""}
