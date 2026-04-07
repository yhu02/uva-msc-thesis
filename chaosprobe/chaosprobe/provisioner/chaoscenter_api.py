"""ChaosCenter GraphQL / REST API mixin for LitmusSetup.

Low-level HTTP client, authentication, and CRUD operations for
environments, infrastructures, experiments, and resilience probes.
"""

import json as _json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Optional


class _ChaosCenterAPIMixin:
    """ChaosCenter API methods mixed into LitmusSetup."""

    CHAOSCENTER_AUTH_PORT = 9003
    CHAOSCENTER_MANAGED_PASS = "ChaosProbe1!"

    # ------------------------------------------------------------------
    # HTTP / GraphQL transport
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Environment / Infrastructure CRUD
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Experiment CRUD
    # ------------------------------------------------------------------

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
