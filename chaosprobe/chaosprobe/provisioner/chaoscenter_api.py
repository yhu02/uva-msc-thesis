"""ChaosCenter GraphQL / REST API mixin for LitmusSetup.

Low-level HTTP client, authentication, and CRUD operations for
environments, infrastructures, experiments, and resilience probes.
"""

import json as _json
import logging
import os
import secrets
import string
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from chaosprobe.provisioner._setup_base import _LitmusSetupBase

logger = logging.getLogger(__name__)

# The managed ChaosCenter admin password is resolved at runtime, never
# committed.  Override with this env var, or let ChaosProbe generate one and
# persist it so the value is stable across runs for a given instance.
CHAOSCENTER_PASSWORD_ENV = "CHAOSPROBE_CHAOSCENTER_PASSWORD"
CHAOSCENTER_PASSWORD_FILE = Path.home() / ".chaosprobe" / "chaoscenter-admin-password"

# ChaosCenter (litmus 3.x) enforces a password policy: 8–16 characters with at
# least one digit, lowercase, uppercase, and special character. A password that
# violates it makes the default→managed rotation fail (and litmus then refuses
# project creation until the default password is changed), so the managed
# password MUST be policy-compliant. (token_urlsafe produces 24 chars with no
# complexity guarantee — non-compliant.)
_PASSWORD_SPECIALS = "!@#$%^&*"
_PASSWORD_LEN = 16  # within the 8–16 window, max entropy


def _is_policy_compliant(pwd: str) -> bool:
    """True if ``pwd`` satisfies ChaosCenter's 8–16 char + complexity policy.

    "Special" is any non-alphanumeric character — broader than the subset
    :func:`_generate_compliant_password` draws from — so a user-supplied env/file
    password using a different special (e.g. ``+``) is not falsely rejected and
    needlessly regenerated.
    """
    return (
        8 <= len(pwd) <= 16
        and any(c.isdigit() for c in pwd)
        and any(c.islower() for c in pwd)
        and any(c.isupper() for c in pwd)
        and any(not c.isalnum() for c in pwd)
    )


def _generate_compliant_password() -> str:
    """A random password meeting ChaosCenter's policy (one of each class)."""
    rng = secrets.SystemRandom()
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(_PASSWORD_SPECIALS),
    ]
    pool = string.ascii_letters + string.digits + _PASSWORD_SPECIALS
    rest = [secrets.choice(pool) for _ in range(_PASSWORD_LEN - len(required))]
    chars = required + rest
    rng.shuffle(chars)
    return "".join(chars)


def _resolve_managed_password() -> str:
    """Resolve the managed ChaosCenter admin password without committing it.

    Resolution order: the ``CHAOSPROBE_CHAOSCENTER_PASSWORD`` env var, then a
    previously-persisted ``~/.chaosprobe`` file, then a freshly generated secret
    that is persisted to that file with ``0600`` perms.  The password must be
    stable across runs — a provisioned ChaosCenter keeps whatever it was rotated
    to — so it is cached on disk rather than baked into source, where a fixed
    default would be a master key for every deployment the tool ever manages.
    """
    env = os.environ.get(CHAOSCENTER_PASSWORD_ENV)
    if env:
        return env
    try:
        if CHAOSCENTER_PASSWORD_FILE.exists():
            existing = CHAOSCENTER_PASSWORD_FILE.read_text().strip()
            # Reuse a persisted value only if it satisfies ChaosCenter's policy.
            # A previously-generated token_urlsafe(18) is 24 chars (non-compliant)
            # and would fail the default→managed rotation, leaving ChaosCenter on
            # its default password and blocking project creation; migrate it.
            if existing and _is_policy_compliant(existing):
                # Re-harden perms before reuse: the file holds the admin password,
                # but may have been created manually or had its mode changed, so a
                # persisted-value path that skipped this could leave it world-readable.
                try:
                    CHAOSCENTER_PASSWORD_FILE.chmod(0o600)
                except OSError:
                    logger.debug("could not re-harden password file perms", exc_info=True)
                return existing
    except OSError:
        logger.debug("could not read managed ChaosCenter password file", exc_info=True)
    pwd = _generate_compliant_password()
    try:
        CHAOSCENTER_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHAOSCENTER_PASSWORD_FILE.write_text(pwd)
        CHAOSCENTER_PASSWORD_FILE.chmod(0o600)
    except OSError:
        logger.debug("could not persist managed ChaosCenter password", exc_info=True)
    return pwd


class _ChaosCenterAPIMixin(_LitmusSetupBase):
    """ChaosCenter API methods mixed into LitmusSetup."""

    CHAOSCENTER_AUTH_PORT = 9003

    @property
    def CHAOSCENTER_MANAGED_PASS(self) -> str:
        """Admin password ChaosProbe rotates the factory default to.

        Resolved once per instance via :func:`_resolve_managed_password` (env
        var → persisted file → generated secret); never a source-committed
        default.
        """
        cached: Optional[str] = getattr(self, "_managed_pass", None)
        if cached is None:
            cached = _resolve_managed_password()
            self._managed_pass = cached
        return cached

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
        timeout: int = 30,
    ) -> dict:
        """Make an HTTP request to the ChaosCenter API.

        Args:
            url: Full URL including endpoint path.
            method: HTTP method.
            data: JSON-serialisable body (for POST/PUT).
            token: Bearer token for authenticated requests.
            headers: Additional HTTP headers.
            timeout: HTTP request timeout in seconds.

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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as e:
            body_text = e.read().decode() if e.fp else ""
            raise RuntimeError(f"ChaosCenter API error {e.code}: {body_text}") from e

        # A 200 response is not guaranteed to be JSON: a proxy/gateway error
        # page or an empty body would otherwise raise a bare JSONDecodeError.
        try:
            # result is decoded JSON (Any); coerce at this boundary
            result: dict = _json.loads(raw)
        except _json.JSONDecodeError as e:
            snippet = raw[:200]
            raise RuntimeError(f"ChaosCenter returned a non-JSON response: {snippet!r}") from e

        # Surface GraphQL-level errors that arrive with HTTP 200
        if isinstance(result, dict) and result.get("errors") and result.get("data") is None:
            errors = result["errors"]
            msg = errors[0].get("message", str(errors)) if errors else str(result)
            raise RuntimeError(f"ChaosCenter GraphQL error: {msg}")
        return result

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _chaoscenter_authenticate(
        self,
        server_url: str,
        username: str,
        password: str,
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
        token = resp.get("accessToken") or resp.get("access_token") or resp.get("token")
        if not token:
            raise RuntimeError("Failed to obtain ChaosCenter access token")
        return resp

    def _chaoscenter_change_password(
        self,
        auth_url: str,
        username: str,
        old_password: str,
        new_password: str,
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

    def _chaoscenter_create_project(
        self,
        auth_url: str,
        project_name: str,
        token: str,
    ) -> None:
        """Create a ChaosCenter project for the authenticated user.

        A freshly-installed ChaosCenter (litmus 3.x) gives the admin no default
        project, so login returns an empty ``projectID`` and every GraphQL call
        that needs one fails. Creating a project here completes the bootstrap;
        the user's subsequent login then returns its ``projectID``. litmus
        refuses this until the default password has been changed, so the managed
        password must already be in effect (see :func:`_resolve_managed_password`).
        """
        self._chaoscenter_api_request(
            f"{auth_url}/create_project",
            data={"projectName": project_name},
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
                # token is read from the JSON login response (Any | None);
                # _chaoscenter_authenticate already raised if it was absent,
                # so coerce to a concrete str at this boundary.
                token: str = (
                    resp.get("accessToken") or resp.get("access_token") or resp.get("token") or ""
                )
                project_id = resp.get("projectID", "")

                # Auto-rotate factory default → managed password
                if pwd == self.CHAOSCENTER_DEFAULT_PASS and pwd != self.CHAOSCENTER_MANAGED_PASS:
                    try:
                        self._chaoscenter_change_password(
                            auth_url,
                            username,
                            self.CHAOSCENTER_DEFAULT_PASS,
                            self.CHAOSCENTER_MANAGED_PASS,
                            token=token,
                        )
                        # Re-login with the new password
                        resp2 = self._chaoscenter_authenticate(
                            auth_url,
                            username,
                            self.CHAOSCENTER_MANAGED_PASS,
                        )
                        token = (
                            resp2.get("accessToken")
                            or resp2.get("access_token")
                            or resp2.get("token")
                            or ""
                        )
                        project_id = resp2.get("projectID", project_id)
                        print("  ChaosCenter: default password rotated to managed password")
                    except Exception:
                        # keep using the default-password token
                        logger.debug("re-login after password rotation failed", exc_info=True)

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
        self,
        gql_url: str,
        project_id: str,
        token: str,
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
        return (resp.get("data", {}).get("listEnvironments", {}).get("environments")) or []

    def _chaoscenter_list_infras(
        self,
        gql_url: str,
        project_id: str,
        token: str,
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
        return (resp.get("data", {}).get("listInfras", {}).get("infras")) or []

    def _chaoscenter_create_environment(
        self,
        gql_url: str,
        project_id: str,
        env_name: str,
        token: str,
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
        # environment_id is read from the JSON GraphQL response (Any); coerce here
        environment_id: str = (
            resp.get("data", {}).get("createEnvironment", {}).get("environmentID", env_name)
        )
        return environment_id

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
        # result is read from the JSON GraphQL response (Any); coerce here
        result: dict = resp.get("data", {}).get("registerInfra", {})
        if not result.get("infraID"):
            raise RuntimeError("Failed to register infrastructure in ChaosCenter")
        return result

    def _apply_manifest(self, manifest: str, namespace: str) -> None:
        """Write *manifest* to a temp file and ``kubectl apply`` it."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
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
                logger.debug("failed to list infras while polling", exc_info=True)
            time.sleep(5)
        return False

    def _subscriber_diagnostics(self, namespace: str) -> str:
        """Return a short diagnostic string about subscriber pod state."""
        lines = []
        try:
            pods = self.core_api.list_namespaced_pod(
                namespace,
                label_selector="app=subscriber",
            )
            if not pods.items:
                lines.append("  No subscriber pods found in namespace " f"'{namespace}'.")
                # Check if the deployment exists
                try:
                    dep = self.apps_api.read_namespaced_deployment(
                        "subscriber",
                        namespace,
                    )
                    lines.append(
                        f"  Deployment exists: replicas="
                        f"{dep.spec.replicas}, "
                        f"ready={dep.status.ready_replicas}"
                    )
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

    def _chaoscenter_find_experiment_id(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        experiment_name: str,
    ) -> str | None:
        """Find an experiment's ID by name via ``listExperiment``.

        Returns the experiment ID if found, else ``None``.
        """
        try:
            resp = self._chaoscenter_api_request(
                gql_url,
                data={
                    "query": (
                        "query($pid: ID!, $req: ListExperimentRequest!) "
                        "{ listExperiment(projectID: $pid, request: $req) "
                        "{ totalNoOfExperiments experiments { experimentID name } } }"
                    ),
                    "variables": {
                        "pid": project_id,
                        "req": {
                            "filter": {"experimentName": experiment_name},
                            "pagination": {"page": 0, "limit": 5},
                        },
                    },
                },
                token=token,
            )
            experiments = (resp.get("data") or {}).get("listExperiment", {}).get("experiments", [])
            for exp in experiments:
                if exp.get("name") == experiment_name:
                    # experiment_id is read from the JSON response (Any); coerce here
                    experiment_id: Optional[str] = exp.get("experimentID")
                    return experiment_id
        except Exception as exc:
            print(f"    ChaosCenter: could not look up experiment '{experiment_name}': {exc}")
        return None

    def chaoscenter_list_experiments(
        self,
        gql_url: str,
        project_id: str,
        token: str,
    ) -> list[dict]:
        """Return all experiments for the given project.

        Returns:
            List of dicts with ``experimentID`` and ``name`` keys.
        """
        try:
            resp = self._chaoscenter_api_request(
                gql_url,
                data={
                    "query": (
                        "query($pid: ID!, $req: ListExperimentRequest!) "
                        "{ listExperiment(projectID: $pid, request: $req) "
                        "{ totalNoOfExperiments experiments { experimentID name } } }"
                    ),
                    "variables": {
                        "pid": project_id,
                        "req": {"pagination": {"page": 0, "limit": 50}},
                    },
                },
                token=token,
            )
            return ((resp.get("data") or {}).get("listExperiment", {}).get("experiments", [])) or []
        except Exception:
            return []

    def chaoscenter_delete_experiment(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        experiment_id: str,
    ) -> bool:
        """Delete a chaos experiment from ChaosCenter.

        Returns True if deleted, False if not found or already gone.
        """
        try:
            self._chaoscenter_api_request(
                gql_url,
                data={
                    "query": (
                        "mutation($pid: ID!, $eid: String!) "
                        "{ deleteChaosExperiment(projectID: $pid, "
                        "experimentID: $eid) }"
                    ),
                    "variables": {"pid": project_id, "eid": experiment_id},
                },
                token=token,
            )
            return True
        except Exception as exc:
            if "already deleted" in str(exc).lower():
                return True
            print(f"    ChaosCenter: delete failed: {exc}")
            return False

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
        """Save (create or update) a chaos experiment in ChaosCenter.

        Looks up the experiment by name first.  If one already exists,
        its ID is reused so ``saveChaosExperiment`` acts as an update
        (upsert) rather than a conflicting insert.  This avoids errors
        from soft-deleted documents that still occupy MongoDB's unique
        indices.

        Args:
            gql_url: GraphQL endpoint URL.
            project_id: ChaosCenter project ID.
            token: Bearer token.
            infra_id: Registered infrastructure ID.
            experiment_id: Default experiment ID (used for new experiments).
            name: Human-readable experiment name.
            manifest: Argo Workflow manifest YAML string.
            description: Optional description.

        Returns:
            The experiment ID as confirmed by the server.
        """
        # Check if an experiment with this name already exists and reuse
        # its ID so the save mutation updates instead of inserting.
        existing_id = self._chaoscenter_find_experiment_id(
            gql_url,
            project_id,
            token,
            name,
        )
        if existing_id:
            experiment_id = existing_id

        # Mixed-value mapping (str query + nested dicts); annotate so the
        # nested ["variables"]["req"]["id"] reassignment below is indexable.
        save_data: dict[str, Any] = {
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
        }

        try:
            self._chaoscenter_api_request(gql_url, data=save_data, token=token)
            return experiment_id
        except Exception as exc:
            err_msg = str(exc).lower()
            is_dup = (
                "duplicate key" in err_msg
                or "experiment name should be unique" in err_msg
                or "duplicate experiment" in err_msg
            )
            if not is_dup:
                raise

            # The experiment (or a soft-deleted ghost) blocks the save.
            # Try deleting it — ignore "already deleted" — then retry.
            print(f"    ChaosCenter: stale experiment '{name}' blocks save, cleaning up...")
            self.chaoscenter_delete_experiment(
                gql_url,
                project_id,
                token,
                experiment_id,
            )

            # Retry with a fresh ID to sidestep soft-deleted ghosts
            # that still occupy MongoDB's unique index on experiment_id.
            import uuid as _uuid

            fresh_id = str(_uuid.uuid4())
            save_data["variables"]["req"]["id"] = fresh_id
            try:
                self._chaoscenter_api_request(gql_url, data=save_data, token=token)
                return fresh_id
            except Exception:
                logger.debug("retry save with fresh experiment id failed", exc_info=True)

            # Last resort: the name itself is blocked by a soft-deleted
            # ghost.  Nothing in the API can fix this — raise clearly.
            raise RuntimeError(
                f"Cannot save experiment '{name}': a soft-deleted experiment "
                f"with this name exists in ChaosCenter's database and blocks "
                f"new experiments.  Delete it from the MongoDB shell:\n"
                f'  db.chaosExperiments.deleteOne({{name: "{name}", is_removed: true}})'
            ) from exc

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
        # notify_id is read from the JSON GraphQL response (Any); coerce here
        notify_id: str = (resp.get("data") or {}).get("runChaosExperiment", {}).get("notifyID", "")
        return notify_id

    def chaoscenter_get_experiment_run(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        notify_id: str,
        timeout: int = 10,
    ) -> dict[str, Any]:
        """Query the status of a running experiment.

        Args:
            gql_url: GraphQL endpoint URL.
            project_id: ChaosCenter project ID.
            token: Bearer token.
            notify_id: The notifyID returned by ``runChaosExperiment``.
            timeout: HTTP timeout for this poll request (seconds).

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
                    "faultsStopped totalFaults "
                    "executionData } }"
                ),
                "variables": {
                    "pid": project_id,
                    "nid": notify_id,
                },
            },
            token=token,
            timeout=timeout,
        )
        # run is read from the JSON GraphQL response (Any); coerce here
        run: dict[str, Any] = (resp.get("data") or {}).get("getExperimentRun", {})
        return run

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
        # probe is read from the JSON GraphQL response (Any); coerce here
        probe: dict[str, Any] = (resp.get("data") or {}).get("addProbe", {})
        return probe

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
        # confirmation is read from the JSON GraphQL response (Any); coerce here
        confirmation: str = (resp.get("data") or {}).get("updateProbe", "")
        return confirmation

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
                "query": ("query($pid: ID!) " "{ listProbes(projectID: $pid) { name type } }"),
                "variables": {"pid": project_id},
            },
            token=token,
        )
        # probes is read from the JSON GraphQL response (Any); coerce here
        probes: list[dict[str, Any]] = (resp.get("data") or {}).get("listProbes", [])
        return probes
