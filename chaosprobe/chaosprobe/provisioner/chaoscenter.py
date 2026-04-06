"""ChaosCenter dashboard management methods for LitmusSetup (mixin).

Covers installation, authentication, environment/infra registration,
experiment save/run/query via the ChaosCenter GraphQL API.
"""

import json as _json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from kubernetes.client.rest import ApiException


class _ChaosCenterMixin:
    """ChaosCenter management methods mixed into LitmusSetup."""

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
                    "helm", "repo", "add", "litmuschaos",
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
                    "helm", "upgrade", "--install",
                    self.CHAOSCENTER_RELEASE_NAME,
                    self.CHAOSCENTER_HELM_CHART,
                    "--namespace", self.LITMUS_NAMESPACE,
                    "--set", f"portal.frontend.service.type={service_type}",
                    "--set", f"portal.server.service.type={service_type}",
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
                self.CHAOSCENTER_FRONTEND_SVC, self.LITMUS_NAMESPACE,
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

    def _chaoscenter_api_request(
        self,
        url: str,
        method: str = "POST",
        data: Optional[dict] = None,
        token: Optional[str] = None,
        headers: Optional[dict] = None,
    ) -> dict:
        """Make an HTTP request to the ChaosCenter API.

        Args:
            url: Full URL including endpoint path.
            method: HTTP method.
            data: JSON-serialisable body (for POST/PUT).
            token: Bearer token for authenticated requests.
            headers: Additional HTTP headers.

        Returns:
            Parsed JSON response as a dict.
        """
        body = _json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = _json.loads(resp.read().decode())
            # Surface GraphQL-level errors that arrive with HTTP 200
            if (
                isinstance(result, dict)
                and result.get("errors")
                and result.get("data") is None
            ):
                errors = result["errors"]
                msg = (
                    errors[0].get("message", str(errors))
                    if errors
                    else str(result)
                )
                raise RuntimeError(f"ChaosCenter GraphQL error: {msg}")
            return result
        except urllib.error.HTTPError as e:
            body_text = e.read().decode() if e.fp else ""
            raise RuntimeError(
                f"ChaosCenter API error {e.code}: {body_text}"
            ) from e

    def _chaoscenter_authenticate(
        self, server_url: str, username: str, password: str,
    ) -> dict:
        """Authenticate against ChaosCenter and return login response.

        Args:
            server_url: Base URL of the auth server (e.g. ``http://host:port``).
            username: ChaosCenter username.
            password: ChaosCenter password.

        Returns:
            Dict with ``accessToken``, ``projectID``, and other keys.
        """
        resp = self._chaoscenter_api_request(
            f"{server_url}/login",
            data={"username": username, "password": password},
        )
        token = (
            resp.get("accessToken")
            or resp.get("access_token")
            or resp.get("token")
        )
        if not token:
            raise RuntimeError("Failed to obtain ChaosCenter access token")
        return resp

    CHAOSCENTER_AUTH_PORT = 9003
    CHAOSCENTER_MANAGED_PASS = "ChaosProbe1!"

    def _chaoscenter_change_password(
        self, auth_url: str, username: str, old_password: str, new_password: str,
        token: str = "",
    ) -> None:
        """Change ChaosCenter password via the auth API."""
        self._chaoscenter_api_request(
            f"{auth_url}/update/password",
            data={
                "username": username,
                "oldPassword": old_password,
                "newPassword": new_password,
            },
            token=token,
        )

    def _chaoscenter_gql_url(self, base_host: str) -> str:
        """Return the GraphQL endpoint URL for a given host."""
        return f"{base_host}:{self.CHAOSCENTER_SERVER_PORT}/query"

    def _chaoscenter_auth_url(self, base_host: str) -> str:
        """Return the auth server base URL for a given host."""
        return f"{base_host}:{self.CHAOSCENTER_AUTH_PORT}"

    def _chaoscenter_login(
        self,
        auth_url: str,
        username: str = "",
        password: str = "",
    ) -> tuple[str, str]:
        """Authenticate and return (token, project_id).

        Tries the provided password first, then the managed password,
        then the factory default.  If the factory default works the
        password is automatically rotated to the managed password.
        """
        username = username or self.CHAOSCENTER_DEFAULT_USER
        candidates = []
        if password:
            candidates.append(password)
        if self.CHAOSCENTER_MANAGED_PASS not in candidates:
            candidates.append(self.CHAOSCENTER_MANAGED_PASS)
        if self.CHAOSCENTER_DEFAULT_PASS not in candidates:
            candidates.append(self.CHAOSCENTER_DEFAULT_PASS)

        last_err: Optional[Exception] = None
        for pwd in candidates:
            try:
                resp = self._chaoscenter_authenticate(auth_url, username, pwd)
                token = (
                    resp.get("accessToken")
                    or resp.get("access_token")
                    or resp.get("token")
                )
                project_id = resp.get("projectID", "")

                # Auto-rotate factory default → managed password
                if pwd == self.CHAOSCENTER_DEFAULT_PASS and pwd != self.CHAOSCENTER_MANAGED_PASS:
                    try:
                        self._chaoscenter_change_password(
                            auth_url, username,
                            self.CHAOSCENTER_DEFAULT_PASS,
                            self.CHAOSCENTER_MANAGED_PASS,
                            token=token,
                        )
                        # Re-login with the new password
                        resp2 = self._chaoscenter_authenticate(
                            auth_url, username, self.CHAOSCENTER_MANAGED_PASS,
                        )
                        token = (
                            resp2.get("accessToken")
                            or resp2.get("access_token")
                            or resp2.get("token")
                        )
                        project_id = resp2.get("projectID", project_id)
                        print(
                            "  ChaosCenter: default password rotated to managed password"
                        )
                    except Exception:
                        pass  # keep using the default-password token

                return token, project_id
            except Exception as exc:
                last_err = exc

        raise RuntimeError(
            f"ChaosCenter authentication failed (tried {len(candidates)} passwords): {last_err}"
        )

    def _chaoscenter_list_environments(
        self, gql_url: str, project_id: str, token: str,
    ) -> list[dict]:
        """Return existing environments for the given project."""
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "query($pid: ID!) { listEnvironments(projectID: $pid) "
                    "{ environments { environmentID name } } }"
                ),
                "variables": {"pid": project_id},
            },
            token=token,
        )
        return (
            resp.get("data", {})
            .get("listEnvironments", {})
            .get("environments")
        ) or []

    def _chaoscenter_list_infras(
        self, gql_url: str, project_id: str, token: str,
    ) -> list[dict]:
        """Return registered infrastructures for the given project."""
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "query($pid: ID!) { listInfras(projectID: $pid) "
                    "{ infras { infraID name environmentID isActive "
                    "isInfraConfirmed infraNamespace } } }"
                ),
                "variables": {"pid": project_id},
            },
            token=token,
        )
        return (
            resp.get("data", {})
            .get("listInfras", {})
            .get("infras")
        ) or []

    def _chaoscenter_create_environment(
        self, gql_url: str, project_id: str, env_name: str, token: str,
    ) -> str:
        """Create a ChaosCenter environment and return its ID."""
        env_query = (
            "mutation($pid: ID!, $req: CreateEnvironmentRequest!) "
            "{ createEnvironment(projectID: $pid, request: $req) "
            "{ environmentID } }"
        )
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": env_query,
                "variables": {
                    "pid": project_id,
                    "req": {
                        "name": env_name,
                        "environmentID": env_name,
                        "type": "NON_PROD",
                    },
                },
            },
            token=token,
        )
        return (
            resp.get("data", {})
            .get("createEnvironment", {})
            .get("environmentID", env_name)
        )

    def _chaoscenter_server_internal_url(self) -> str:
        """Return the cluster-internal URL of the ChaosCenter frontend.

        The ChaosCenter server derives ``SERVER_ADDR`` by appending
        ``/api/query`` to the ``Referer`` header.  Inside the cluster the
        subscriber must reach the server through the **frontend** service
        (which proxies ``/api/`` to the GraphQL server), so we use the
        frontend service DNS name here.
        """
        return (
            f"http://{self.CHAOSCENTER_FRONTEND_SVC}"
            f".{self.LITMUS_NAMESPACE}.svc.cluster.local"
            f":{self.CHAOSCENTER_FRONTEND_PORT}"
        )

    def _chaoscenter_register_infra(
        self,
        gql_url: str,
        project_id: str,
        env_id: str,
        namespace: str,
        token: str,
    ) -> dict:
        """Register namespace as infrastructure and return {infraID, manifest}."""
        infra_query = (
            "mutation($pid: ID!, $req: RegisterInfraRequest!) "
            "{ registerInfra(projectID: $pid, request: $req) "
            "{ infraID manifest token } }"
        )
        # The server reads the Referer header to build the SERVER_ADDR
        # that the subscriber uses *inside the cluster*.  Must be the
        # cluster-internal service URL, not a localhost port-forward.
        referer = self._chaoscenter_server_internal_url()
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": infra_query,
                "variables": {
                    "pid": project_id,
                    "req": {
                        "name": f"chaosprobe-{namespace}",
                        "environmentID": env_id,
                        "description": f"ChaosProbe infra for {namespace}",
                        "infraNamespace": namespace,
                        "infraScope": "namespace",
                        "infrastructureType": "Kubernetes",
                        "platformName": "kubernetes",
                        "infraNsExists": True,
                        "skipSsl": True,
                    },
                },
            },
            token=token,
            headers={"Referer": referer},
        )
        result = resp.get("data", {}).get("registerInfra", {})
        if not result.get("infraID"):
            raise RuntimeError("Failed to register infrastructure in ChaosCenter")
        return result

    def _apply_manifest(self, manifest: str, namespace: str) -> None:
        """Write *manifest* to a temp file and ``kubectl apply`` it."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False,
        ) as f:
            f.write(manifest)
            f.flush()
            try:
                subprocess.run(
                    ["kubectl", "apply", "-f", f.name, "-n", namespace],
                    check=True,
                    capture_output=True,
                )
            finally:
                os.unlink(f.name)

    def _wait_for_infra_active(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        infra_id: str,
        timeout: int = 60,
    ) -> bool:
        """Poll ``listInfras`` until *infra_id* has ``isActive=True``.

        The subscriber pod can be Running+Ready before its WebSocket
        connection to ChaosCenter is established.  This helper bridges
        that gap so experiments are not submitted against inactive infra.
        """
        start = time.time()
        while time.time() - start < timeout:
            try:
                infras = self._chaoscenter_list_infras(gql_url, project_id, token)
                for i in infras:
                    if i.get("infraID") == infra_id and i.get("isActive"):
                        return True
            except Exception:
                pass
            time.sleep(5)
        return False

    def _subscriber_diagnostics(self, namespace: str) -> str:
        """Return a short diagnostic string about subscriber pod state."""
        lines = []
        try:
            pods = self.core_api.list_namespaced_pod(
                namespace, label_selector="app=subscriber",
            )
            if not pods.items:
                lines.append("  No subscriber pods found in namespace "
                             f"'{namespace}'.")
                # Check if the deployment exists
                try:
                    dep = self.apps_api.read_namespaced_deployment(
                        "subscriber", namespace,
                    )
                    lines.append(f"  Deployment exists: replicas="
                                 f"{dep.spec.replicas}, "
                                 f"ready={dep.status.ready_replicas}")
                except Exception:
                    lines.append("  Subscriber deployment not found.")
            else:
                for pod in pods.items:
                    phase = pod.status.phase or "Unknown"
                    cs = pod.status.container_statuses or []
                    waiting_reasons = []
                    for c in cs:
                        if c.state and c.state.waiting:
                            waiting_reasons.append(
                                f"{c.name}: {c.state.waiting.reason}"
                                f" ({c.state.waiting.message or 'no message'})"
                            )
                    line = f"  Pod {pod.metadata.name}: phase={phase}"
                    if waiting_reasons:
                        line += f", waiting=[{'; '.join(waiting_reasons)}]"
                    lines.append(line)
        except Exception as e:
            lines.append(f"  Could not query pods: {e}")
        lines.append(f"  Check: kubectl logs -n {namespace} -l app=subscriber")
        return "\n".join(lines)

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
            auth_url, username=username, password=password,
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
            i for i in infras
            if i.get("infraNamespace") != namespace
            and i.get("infraNamespace")  # skip entries without a namespace
        ]
        for other in other_infras:
            other_ns = other["infraNamespace"]
            infra_deployments = [
                "chaos-exporter", "chaos-operator-ce", "event-tracker",
                "subscriber", "workflow-controller",
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
                            dep_name, other_ns,
                        )
                    except ApiException:
                        pass

        existing = [
            i for i in infras
            if i.get("infraNamespace") == namespace
            and i.get("environmentID") == env_name
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
            # Ensure subscriber deployment exists — it may have been
            # evicted, deleted, or never applied (e.g. namespace was
            # recreated).  Always check regardless of isInfraConfirmed.
            subscriber_exists = False
            try:
                self.apps_api.read_namespaced_deployment(
                    "subscriber", namespace,
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
                    manifest = (
                        manifest_resp.get("data", {})
                        .get("getInfraManifest", "")
                    )
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
                        c.ready
                        for p in pods.items
                        for c in (p.status.container_statuses or [])
                    ):
                        print("  ChaosCenter: subscriber pod ready")
                        break
                except Exception:
                    pass
                time.sleep(5)
            else:
                # Collect diagnostic info
                diag = self._subscriber_diagnostics(namespace)
                raise RuntimeError(
                    f"Subscriber pod not ready after {timeout}s.\n{diag}"
                )
        else:
            # No infra exists — register a new one
            result = self._chaoscenter_register_infra(
                gql_url, project_id, env_name, namespace, token,
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
                        c.ready
                        for p in pods.items
                        for c in (p.status.container_statuses or [])
                    ):
                        print("  ChaosCenter: subscriber pod ready")
                        break
                except Exception:
                    pass
                time.sleep(5)
            else:
                diag = self._subscriber_diagnostics(namespace)
                raise RuntimeError(
                    f"Subscriber pod not ready after {timeout}s.\n{diag}"
                )

        # --- wait for infrastructure to become active --------------------
        # The subscriber pod can be Running+Ready before its WebSocket
        # to ChaosCenter is established.  Poll until isActive flips.
        if not (existing and existing[0].get("isActive")):
            print("  ChaosCenter: waiting for infrastructure to become active...")
            active_timeout = min(timeout, 120)
            if self._wait_for_infra_active(
                gql_url, project_id, token, infra_id, timeout=active_timeout,
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
            raise RuntimeError(
                "Cannot detect ChaosCenter URL. Is it installed and ready?"
            )

        # Derive scheme + host (strip port)
        base_host = base_url.rsplit(":", 1)[0]

        result = self.ensure_chaoscenter_configured(
            namespace=namespace,
            base_host=base_host,
            username=username,
            password=password,
        )
        return {"infra_id": result["infra_id"], "manifest": ""}

    def chaoscenter_save_experiment(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        infra_id: str,
        experiment_id: str,
        name: str,
        manifest: str,
        description: str = "",
    ) -> str:
        """Save a chaos experiment in ChaosCenter.

        Args:
            gql_url: GraphQL endpoint URL.
            project_id: ChaosCenter project ID.
            token: Bearer token.
            infra_id: Registered infrastructure ID.
            experiment_id: Unique experiment ID.
            name: Human-readable experiment name.
            manifest: Argo Workflow manifest YAML string.
            description: Optional description.

        Returns:
            The experiment ID as confirmed by the server.
        """
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "mutation($pid: ID!, $req: SaveChaosExperimentRequest!) "
                    "{ saveChaosExperiment(projectID: $pid, request: $req) }"
                ),
                "variables": {
                    "pid": project_id,
                    "req": {
                        "id": experiment_id,
                        "type": "Experiment",
                        "name": name,
                        "description": description or f"ChaosProbe experiment: {name}",
                        "manifest": manifest,
                        "infraID": infra_id,
                        "tags": ["chaosprobe"],
                    },
                },
            },
            token=token,
        )
        return (resp.get("data") or {}).get("saveChaosExperiment", experiment_id)

    def chaoscenter_run_experiment(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        experiment_id: str,
    ) -> str:
        """Trigger execution of a saved chaos experiment.

        Args:
            gql_url: GraphQL endpoint URL.
            project_id: ChaosCenter project ID.
            token: Bearer token.
            experiment_id: ID of the experiment to run.

        Returns:
            The notifyID for tracking the experiment run.
        """
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "mutation($eid: String!, $pid: ID!) "
                    "{ runChaosExperiment(experimentID: $eid, projectID: $pid) "
                    "{ notifyID } }"
                ),
                "variables": {
                    "eid": experiment_id,
                    "pid": project_id,
                },
            },
            token=token,
        )
        return (
            (resp.get("data") or {})
            .get("runChaosExperiment", {})
            .get("notifyID", "")
        )

    def chaoscenter_get_experiment_run(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        notify_id: str,
    ) -> dict[str, Any]:
        """Query the status of a running experiment.

        Args:
            gql_url: GraphQL endpoint URL.
            project_id: ChaosCenter project ID.
            token: Bearer token.
            notify_id: The notifyID returned by ``runChaosExperiment``.

        Returns:
            Dict with at least ``phase`` key (e.g. ``Running``,
            ``Completed``, ``Error``).  Also includes
            ``resiliencyScore``, ``faultsPassed``, ``faultsFailed``,
            ``totalFaults`` when available.
        """
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "query($pid: ID!, $nid: ID) "
                    "{ getExperimentRun(projectID: $pid, notifyID: $nid) "
                    "{ experimentRunID phase resiliencyScore "
                    "faultsPassed faultsFailed faultsAwaited "
                    "faultsStopped totalFaults } }"
                ),
                "variables": {
                    "pid": project_id,
                    "nid": notify_id,
                },
            },
            token=token,
        )
        return (resp.get("data") or {}).get("getExperimentRun", {})

    # ------------------------------------------------------------------
    # Resilience Probes — register / query via ChaosCenter API
    # ------------------------------------------------------------------

    def chaoscenter_add_probe(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        probe_request: dict[str, Any],
    ) -> dict[str, Any]:
        """Register a resilience probe with ChaosCenter.

        Args:
            gql_url: GraphQL endpoint URL.
            project_id: ChaosCenter project ID.
            token: Bearer token.
            probe_request: ``ProbeRequest`` input matching the GraphQL schema.
                Must include ``name``, ``type``, ``infrastructureType``, and
                the relevant properties key (e.g. ``kubernetesHTTPProperties``).

        Returns:
            The created Probe object dict (``name``, ``type``).
        """
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "mutation($req: ProbeRequest!, $pid: ID!) "
                    "{ addProbe(request: $req, projectID: $pid) "
                    "{ name type } }"
                ),
                "variables": {
                    "req": probe_request,
                    "pid": project_id,
                },
            },
            token=token,
        )
        return (resp.get("data") or {}).get("addProbe", {})

    def chaoscenter_update_probe(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        probe_request: dict[str, Any],
    ) -> str:
        """Update an existing resilience probe in ChaosCenter.

        Args:
            gql_url: GraphQL endpoint URL.
            project_id: ChaosCenter project ID.
            token: Bearer token.
            probe_request: ``ProbeRequest`` input matching the GraphQL schema.

        Returns:
            Confirmation string from the server.
        """
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "mutation($req: ProbeRequest!, $pid: ID!) "
                    "{ updateProbe(request: $req, projectID: $pid) }"
                ),
                "variables": {
                    "req": probe_request,
                    "pid": project_id,
                },
            },
            token=token,
        )
        return (resp.get("data") or {}).get("updateProbe", "")

    def chaoscenter_list_probes(
        self,
        gql_url: str,
        project_id: str,
        token: str,
    ) -> list[dict[str, Any]]:
        """List all registered resilience probes.

        Returns:
            List of probe dicts with ``name`` and ``type`` keys.
        """
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "query($pid: ID!) "
                    "{ listProbes(projectID: $pid) { name type } }"
                ),
                "variables": {"pid": project_id},
            },
            token=token,
        )
        return (resp.get("data") or {}).get("listProbes", [])
