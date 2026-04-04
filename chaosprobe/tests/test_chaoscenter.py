"""Tests for ChaosCenter dashboard integration."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from chaosprobe.provisioner.setup import LitmusSetup

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_setup(**overrides) -> LitmusSetup:
    """Create a LitmusSetup with Kubernetes mocked out."""
    with patch.object(LitmusSetup, "__init__", lambda self, **kw: None):
        setup = LitmusSetup.__new__(LitmusSetup)
        setup._k8s_initialized = True
        setup.core_api = MagicMock()
        setup.apps_api = MagicMock()
        setup.rbac_api = MagicMock()
        setup.apiext_api = MagicMock()
        setup.custom_api = MagicMock()
        for k, v in overrides.items():
            setattr(setup, k, v)
        return setup


def _mock_service(name, svc_type="NodePort", node_port=30091, port=9091):
    """Build a mock Kubernetes service object."""
    svc = MagicMock()
    svc.metadata.name = name
    svc.spec.type = svc_type
    svc.spec.ports = [MagicMock(port=port, node_port=node_port)]
    return svc


def _mock_deployment(name, ready=True, replicas=1):
    dep = MagicMock()
    dep.metadata.name = name
    dep.spec.replicas = replicas
    dep.status.ready_replicas = replicas if ready else 0
    return dep


def _mock_pod(name, phase="Running", ready=True):
    pod = MagicMock()
    pod.metadata.name = name
    pod.status.phase = phase
    cs = MagicMock()
    cs.ready = ready
    pod.status.container_statuses = [cs]
    return pod


def _mock_node(ip="192.168.1.10"):
    node = MagicMock()
    addr = MagicMock()
    addr.type = "InternalIP"
    addr.address = ip
    node.status.addresses = [addr]
    return node


# ---------------------------------------------------------------------------
# is_chaoscenter_installed
# ---------------------------------------------------------------------------


class TestIsChaoscenterInstalled:
    def test_returns_false_when_k8s_not_init(self):
        setup = _make_setup(_k8s_initialized=False)
        assert setup.is_chaoscenter_installed() is False

    def test_returns_true_when_frontend_svc_exists(self):
        setup = _make_setup()
        frontend_svc = _mock_service(LitmusSetup.CHAOSCENTER_FRONTEND_SVC)
        svc_list = MagicMock()
        svc_list.items = [frontend_svc]
        setup.core_api.list_namespaced_service.return_value = svc_list
        assert setup.is_chaoscenter_installed() is True

    def test_returns_false_when_no_frontend_svc(self):
        setup = _make_setup()
        svc_list = MagicMock()
        svc_list.items = [_mock_service("some-other-svc")]
        setup.core_api.list_namespaced_service.return_value = svc_list
        assert setup.is_chaoscenter_installed() is False

    def test_returns_false_on_exception(self):
        setup = _make_setup()
        setup.core_api.list_namespaced_service.side_effect = Exception("fail")
        assert setup.is_chaoscenter_installed() is False


# ---------------------------------------------------------------------------
# is_chaoscenter_ready
# ---------------------------------------------------------------------------


class TestIsChaoscenterReady:
    def test_returns_true_when_all_deployments_ready(self):
        setup = _make_setup()
        # Frontend service must exist first
        svc_list = MagicMock()
        svc_list.items = [_mock_service(LitmusSetup.CHAOSCENTER_FRONTEND_SVC)]
        setup.core_api.list_namespaced_service.return_value = svc_list

        dep_list = MagicMock()
        dep_list.items = [
            _mock_deployment("chaos-litmus-frontend"),
            _mock_deployment("chaos-litmus-server"),
            _mock_deployment("chaos-litmus-auth-server"),
        ]
        setup.apps_api.list_namespaced_deployment.return_value = dep_list
        assert setup.is_chaoscenter_ready() is True

    def test_returns_false_when_frontend_not_ready(self):
        setup = _make_setup()
        svc_list = MagicMock()
        svc_list.items = [_mock_service(LitmusSetup.CHAOSCENTER_FRONTEND_SVC)]
        setup.core_api.list_namespaced_service.return_value = svc_list

        dep_list = MagicMock()
        dep_list.items = [
            _mock_deployment("chaos-litmus-frontend", ready=False),
            _mock_deployment("chaos-litmus-server"),
            _mock_deployment("chaos-litmus-auth-server"),
        ]
        setup.apps_api.list_namespaced_deployment.return_value = dep_list
        assert setup.is_chaoscenter_ready() is False

    def test_returns_false_when_not_installed(self):
        setup = _make_setup()
        svc_list = MagicMock()
        svc_list.items = []
        setup.core_api.list_namespaced_service.return_value = svc_list
        assert setup.is_chaoscenter_ready() is False


# ---------------------------------------------------------------------------
# get_dashboard_url
# ---------------------------------------------------------------------------


class TestGetDashboardUrl:
    def test_nodeport_url(self):
        setup = _make_setup()
        svc = _mock_service(
            LitmusSetup.CHAOSCENTER_FRONTEND_SVC,
            svc_type="NodePort",
            node_port=30091,
        )
        setup.core_api.read_namespaced_service.return_value = svc
        node = _mock_node("10.0.0.5")
        node_list = MagicMock()
        node_list.items = [node]
        setup.core_api.list_node.return_value = node_list

        url = setup.get_dashboard_url()
        assert url == "http://10.0.0.5:30091"

    def test_loadbalancer_url(self):
        setup = _make_setup()
        svc = _mock_service(
            LitmusSetup.CHAOSCENTER_FRONTEND_SVC,
            svc_type="LoadBalancer",
            port=9091,
        )
        ingress_entry = MagicMock()
        ingress_entry.ip = "203.0.113.1"
        ingress_entry.hostname = None
        svc.status.load_balancer = MagicMock()
        svc.status.load_balancer.ingress = [ingress_entry]
        setup.core_api.read_namespaced_service.return_value = svc

        url = setup.get_dashboard_url()
        assert url == "http://203.0.113.1:9091"

    def test_returns_none_when_not_initialized(self):
        setup = _make_setup(_k8s_initialized=False)
        assert setup.get_dashboard_url() is None

    def test_returns_none_on_exception(self):
        setup = _make_setup()
        setup.core_api.read_namespaced_service.side_effect = Exception("api fail")
        assert setup.get_dashboard_url() is None


# ---------------------------------------------------------------------------
# get_chaoscenter_status
# ---------------------------------------------------------------------------


class TestGetChaoscenterStatus:
    def test_not_installed(self):
        setup = _make_setup()
        svc_list = MagicMock()
        svc_list.items = []
        setup.core_api.list_namespaced_service.return_value = svc_list

        status = setup.get_chaoscenter_status()
        assert status["installed"] is False
        assert status["ready"] is False
        assert status["pods"] == []
        assert status["frontend_url"] is None

    def test_installed_and_ready(self):
        setup = _make_setup()
        # is_chaoscenter_installed
        svc_list = MagicMock()
        svc_list.items = [_mock_service(LitmusSetup.CHAOSCENTER_FRONTEND_SVC)]
        setup.core_api.list_namespaced_service.return_value = svc_list

        # is_chaoscenter_ready
        dep_list = MagicMock()
        dep_list.items = [
            _mock_deployment("chaos-litmus-frontend"),
            _mock_deployment("chaos-litmus-server"),
            _mock_deployment("chaos-litmus-auth-server"),
        ]
        setup.apps_api.list_namespaced_deployment.return_value = dep_list

        # list pods
        pod_list = MagicMock()
        pod_list.items = [_mock_pod("chaos-litmus-frontend-abc")]
        setup.core_api.list_namespaced_pod.return_value = pod_list

        # get_dashboard_url
        svc = _mock_service(
            LitmusSetup.CHAOSCENTER_FRONTEND_SVC, svc_type="NodePort", node_port=30091,
        )
        setup.core_api.read_namespaced_service.return_value = svc
        node_list = MagicMock()
        node_list.items = [_mock_node("10.0.0.1")]
        setup.core_api.list_node.return_value = node_list

        status = setup.get_chaoscenter_status()
        assert status["installed"] is True
        assert status["ready"] is True
        assert len(status["pods"]) == 1
        assert status["frontend_url"] == "http://10.0.0.1:30091"


# ---------------------------------------------------------------------------
# install_chaoscenter
# ---------------------------------------------------------------------------


class TestInstallChaoscenter:
    @patch("subprocess.run")
    def test_install_calls_helm(self, mock_run):
        setup = _make_setup()
        # Make _wait_for_chaoscenter return True immediately
        with patch.object(setup, "_wait_for_chaoscenter", return_value=True):
            with patch.object(setup, "_ensure_namespace"):
                result = setup.install_chaoscenter(wait=True, timeout=10)
        assert result is True
        # Verify helm upgrade --install was called
        helm_calls = [
            c for c in mock_run.call_args_list
            if any("upgrade" in str(a) for a in c.args + tuple(c.kwargs.values()))
        ]
        assert len(helm_calls) >= 1

    @patch("subprocess.run")
    def test_install_respects_service_type(self, mock_run):
        setup = _make_setup()
        with patch.object(setup, "_wait_for_chaoscenter", return_value=True):
            with patch.object(setup, "_ensure_namespace"):
                setup.install_chaoscenter(service_type="LoadBalancer")
        # Find the helm install call
        for call in mock_run.call_args_list:
            cmd = call.args[0] if call.args else call.kwargs.get("args", [])
            if "upgrade" in cmd:
                cmd_str = " ".join(cmd)
                assert "LoadBalancer" in cmd_str
                break

    @patch("subprocess.run", side_effect=Exception("helm not found"))
    def test_install_raises_on_failure(self, mock_run):
        setup = _make_setup()
        with patch.object(setup, "_ensure_namespace"):
            with pytest.raises(Exception, match="helm not found"):
                setup.install_chaoscenter()


# ---------------------------------------------------------------------------
# _chaoscenter_api_request
# ---------------------------------------------------------------------------


class TestChaoscenterApiRequest:
    @patch("urllib.request.urlopen")
    def test_post_request(self, mock_urlopen):
        setup = _make_setup()
        response_data = {"data": {"ok": True}}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = setup._chaoscenter_api_request(
            "http://localhost:9002/api/query",
            data={"query": "{ listProjects { projects { projectID } } }"},
            token="test-token",
        )
        assert result == response_data

    @patch("urllib.request.urlopen")
    def test_handles_http_error(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://localhost", code=401, msg="Unauthorized",
            hdrs=None, fp=None,
        )
        setup = _make_setup()
        with pytest.raises(RuntimeError, match="ChaosCenter API error 401"):
            setup._chaoscenter_api_request("http://localhost:9002/api/query")


# ---------------------------------------------------------------------------
# _chaoscenter_authenticate
# ---------------------------------------------------------------------------


class TestChaoscenterAuthenticate:
    def test_returns_full_response(self):
        setup = _make_setup()
        resp = {"accessToken": "jwt-abc", "projectID": "p1"}
        with patch.object(
            setup,
            "_chaoscenter_api_request",
            return_value=resp,
        ):
            result = setup._chaoscenter_authenticate(
                "http://localhost:9003", "admin", "litmus",
            )
        assert result == resp

    def test_uses_login_endpoint(self):
        setup = _make_setup()
        with patch.object(
            setup,
            "_chaoscenter_api_request",
            return_value={"accessToken": "tok"},
        ) as mock_req:
            setup._chaoscenter_authenticate(
                "http://localhost:9003", "admin", "litmus",
            )
        mock_req.assert_called_once()
        url = mock_req.call_args[0][0]
        assert url == "http://localhost:9003/login"

    def test_raises_on_missing_token(self):
        setup = _make_setup()
        with patch.object(
            setup,
            "_chaoscenter_api_request",
            return_value={"error": "bad creds"},
        ):
            with pytest.raises(RuntimeError, match="Failed to obtain"):
                setup._chaoscenter_authenticate(
                    "http://localhost:9003", "admin", "wrong",
                )


# ---------------------------------------------------------------------------
# connect_infrastructure
# ---------------------------------------------------------------------------


class TestConnectInfrastructure:
    def test_successful_registration(self):
        setup = _make_setup()
        with patch.object(setup, "get_dashboard_url", return_value="http://10.0.0.1:30091"):
            with patch.object(
                setup,
                "ensure_chaoscenter_configured",
                return_value={
                    "token": "tok",
                    "project_id": "p1",
                    "environment_id": "chaosprobe-online-boutique",
                    "infra_id": "infra-123",
                },
            ) as mock_ensure:
                result = setup.connect_infrastructure(namespace="online-boutique")
        assert result["infra_id"] == "infra-123"
        mock_ensure.assert_called_once_with(
            namespace="online-boutique",
            base_host="http://10.0.0.1",
            username="",
            password="",
        )

    def test_raises_when_no_dashboard_url(self):
        setup = _make_setup()
        with patch.object(setup, "get_dashboard_url", return_value=None):
            with pytest.raises(RuntimeError, match="Cannot detect ChaosCenter URL"):
                setup.connect_infrastructure(namespace="test")


# ---------------------------------------------------------------------------
# check_prerequisites includes ChaosCenter keys
# ---------------------------------------------------------------------------


class TestCheckPrerequisitesIncludesChaoscenter:
    def test_keys_present(self):
        setup = _make_setup()
        # Stub everything so check_prerequisites doesn't hit real system
        with patch.object(setup, "_check_kubectl", return_value=True), \
             patch.object(setup, "_check_helm", return_value=True), \
             patch.object(setup, "_check_ansible", return_value=False), \
             patch.object(setup, "_check_python_venv", return_value=True), \
             patch.object(setup, "_check_git", return_value=True), \
             patch.object(setup, "_check_ssh", return_value=True), \
             patch.object(setup, "_check_vagrant", return_value=False), \
             patch.object(setup, "_check_libvirt", return_value={"all_ready": False}), \
             patch.object(setup, "_check_cluster_access", return_value=True), \
             patch.object(setup, "is_litmus_installed", return_value=True), \
             patch.object(setup, "is_litmus_ready", return_value=True), \
             patch.object(setup, "is_chaoscenter_installed", return_value=False), \
             patch.object(setup, "is_chaoscenter_ready", return_value=False):
            prereqs = setup.check_prerequisites()

        assert "chaoscenter_installed" in prereqs
        assert "chaoscenter_ready" in prereqs
        assert prereqs["chaoscenter_installed"] is False


# ---------------------------------------------------------------------------
# _wait_for_chaoscenter
# ---------------------------------------------------------------------------


class TestWaitForChaoscenter:
    @patch("time.sleep")
    def test_returns_true_when_ready(self, mock_sleep):
        setup = _make_setup()
        with patch.object(setup, "is_chaoscenter_ready", return_value=True):
            assert setup._wait_for_chaoscenter(timeout=30) is True

    @patch("time.sleep")
    @patch("time.time", side_effect=[0, 0, 100, 100])
    def test_returns_false_on_timeout(self, mock_time, mock_sleep):
        setup = _make_setup()
        pod_list = MagicMock()
        pod_list.items = []
        setup.core_api.list_namespaced_pod.return_value = pod_list
        with patch.object(setup, "is_chaoscenter_ready", return_value=False):
            assert setup._wait_for_chaoscenter(timeout=10) is False


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


class TestDashboardCLI:
    def _runner(self):
        return CliRunner()

    @patch.object(LitmusSetup, "__init__", lambda self, **kw: None)
    def test_dashboard_status_not_installed(self):
        from chaosprobe.cli import main

        with patch.object(LitmusSetup, "is_chaoscenter_installed", return_value=False), \
             patch.object(LitmusSetup, "get_chaoscenter_status", return_value={
                 "installed": False, "ready": False, "pods": [], "frontend_url": None,
             }):
            # Patch _k8s_initialized and required K8s APIs
            with patch.object(LitmusSetup, "_k8s_initialized", True, create=True), \
                 patch.object(LitmusSetup, "core_api", MagicMock(), create=True), \
                 patch.object(LitmusSetup, "apps_api", MagicMock(), create=True):
                result = self._runner().invoke(main, ["dashboard", "status"])
        assert result.exit_code == 0
        assert "not installed" in result.output.lower()

    @patch.object(LitmusSetup, "__init__", lambda self, **kw: None)
    def test_dashboard_credentials(self):
        from chaosprobe.cli import main

        result = self._runner().invoke(main, ["dashboard", "credentials"])
        assert result.exit_code == 0
        assert "admin" in result.output
        assert "litmus" in result.output

    @patch.object(LitmusSetup, "__init__", lambda self, **kw: None)
    def test_dashboard_open_not_installed(self):
        from chaosprobe.cli import main

        with patch.object(LitmusSetup, "is_chaoscenter_installed", return_value=False), \
             patch.object(LitmusSetup, "_k8s_initialized", True, create=True), \
             patch.object(LitmusSetup, "core_api", MagicMock(), create=True), \
             patch.object(LitmusSetup, "apps_api", MagicMock(), create=True):
            result = self._runner().invoke(main, ["dashboard", "open"])
        assert result.exit_code != 0
        assert "not installed" in result.output.lower()

    @patch.object(LitmusSetup, "__init__", lambda self, **kw: None)
    def test_dashboard_open_with_url(self):
        from chaosprobe.cli import main

        with patch.object(LitmusSetup, "is_chaoscenter_installed", return_value=True), \
             patch.object(LitmusSetup, "get_dashboard_url", return_value="http://10.0.0.1:30091"), \
             patch.object(LitmusSetup, "_k8s_initialized", True, create=True), \
             patch.object(LitmusSetup, "core_api", MagicMock(), create=True), \
             patch.object(LitmusSetup, "apps_api", MagicMock(), create=True):
            result = self._runner().invoke(main, ["dashboard", "open"])
        assert result.exit_code == 0
        assert "http://10.0.0.1:30091" in result.output

    @patch.object(LitmusSetup, "__init__", lambda self, **kw: None)
    def test_dashboard_install_already_installed(self):
        from chaosprobe.cli import main

        with patch.object(LitmusSetup, "is_chaoscenter_installed", return_value=True), \
             patch.object(LitmusSetup, "get_dashboard_url", return_value="http://10.0.0.1:30091"), \
             patch.object(LitmusSetup, "_k8s_initialized", True, create=True), \
             patch.object(LitmusSetup, "core_api", MagicMock(), create=True), \
             patch.object(LitmusSetup, "apps_api", MagicMock(), create=True):
            result = self._runner().invoke(main, ["dashboard", "install"])
        assert result.exit_code == 0
        assert "already installed" in result.output.lower()


# ---------------------------------------------------------------------------
# _chaoscenter_login — password cascade & auto-rotation
# ---------------------------------------------------------------------------


class TestChaoscenterLogin:
    def test_login_with_provided_password(self):
        setup = _make_setup()
        with patch.object(
            setup,
            "_chaoscenter_authenticate",
            return_value={"accessToken": "tok", "projectID": "p1"},
        ):
            token, pid = setup._chaoscenter_login(
                "http://localhost:9003", password="custom",
            )
        assert token == "tok"
        assert pid == "p1"

    def test_login_falls_back_to_managed_password(self):
        setup = _make_setup()
        calls = []

        def fake_auth(url, user, pwd):
            calls.append(pwd)
            if pwd == LitmusSetup.CHAOSCENTER_MANAGED_PASS:
                return {"accessToken": "tok2", "projectID": "p2"}
            raise RuntimeError("bad")

        with patch.object(setup, "_chaoscenter_authenticate", side_effect=fake_auth):
            token, pid = setup._chaoscenter_login("http://localhost:9003")
        assert token == "tok2"
        assert LitmusSetup.CHAOSCENTER_MANAGED_PASS in calls

    def test_login_auto_rotates_default_password(self):
        setup = _make_setup()
        auth_calls = []

        def fake_auth(url, user, pwd):
            auth_calls.append(pwd)
            # Both managed and default work, but managed is tried first
            if pwd == LitmusSetup.CHAOSCENTER_MANAGED_PASS:
                raise RuntimeError("bad")
            return {"accessToken": "tok", "projectID": "p1"}

        with patch.object(setup, "_chaoscenter_authenticate", side_effect=fake_auth):
            with patch.object(setup, "_chaoscenter_change_password") as mock_change:
                token, pid = setup._chaoscenter_login("http://localhost:9003")
        # Default password succeeded — should attempt rotation
        mock_change.assert_called_once()

    def test_login_raises_when_all_passwords_fail(self):
        setup = _make_setup()
        with patch.object(
            setup,
            "_chaoscenter_authenticate",
            side_effect=RuntimeError("bad"),
        ):
            with pytest.raises(RuntimeError, match="authentication failed"):
                setup._chaoscenter_login("http://localhost:9003")


# ---------------------------------------------------------------------------
# _chaoscenter_list_environments / _chaoscenter_list_infras
# ---------------------------------------------------------------------------


class TestChaoscenterListHelpers:
    def test_list_environments(self):
        setup = _make_setup()
        with patch.object(
            setup,
            "_chaoscenter_api_request",
            return_value={
                "data": {
                    "listEnvironments": {
                        "environments": [
                            {"environmentID": "env1", "name": "env1"},
                        ]
                    }
                }
            },
        ):
            envs = setup._chaoscenter_list_environments(
                "http://localhost:9002/query", "pid", "tok",
            )
        assert len(envs) == 1
        assert envs[0]["environmentID"] == "env1"

    def test_list_infras(self):
        setup = _make_setup()
        with patch.object(
            setup,
            "_chaoscenter_api_request",
            return_value={
                "data": {
                    "listInfras": {
                        "infras": [
                            {
                                "infraID": "i1",
                                "name": "n",
                                "environmentID": "e",
                                "isActive": True,
                                "isInfraConfirmed": True,
                                "infraNamespace": "ns",
                            },
                        ]
                    }
                }
            },
        ):
            infras = setup._chaoscenter_list_infras(
                "http://localhost:9002/query", "pid", "tok",
            )
        assert len(infras) == 1
        assert infras[0]["infraID"] == "i1"


# ---------------------------------------------------------------------------
# _chaoscenter_create_environment / _chaoscenter_register_infra
# ---------------------------------------------------------------------------


class TestChaoscenterMutationHelpers:
    def test_create_environment(self):
        setup = _make_setup()
        with patch.object(
            setup,
            "_chaoscenter_api_request",
            return_value={
                "data": {"createEnvironment": {"environmentID": "my-env"}}
            },
        ):
            eid = setup._chaoscenter_create_environment(
                "http://localhost:9002/query", "pid", "my-env", "tok",
            )
        assert eid == "my-env"

    def test_register_infra(self):
        setup = _make_setup()
        with patch.object(
            setup,
            "_chaoscenter_api_request",
            return_value={
                "data": {
                    "registerInfra": {
                        "infraID": "inf1",
                        "manifest": "yaml-content",
                        "token": "t",
                    }
                }
            },
        ):
            result = setup._chaoscenter_register_infra(
                "http://localhost:9002/query", "pid", "env1", "ns", "tok",
            )
        assert result["infraID"] == "inf1"
        assert result["manifest"] == "yaml-content"

    def test_register_infra_raises_on_failure(self):
        setup = _make_setup()
        with patch.object(
            setup,
            "_chaoscenter_api_request",
            return_value={"data": {"registerInfra": {}}},
        ):
            with pytest.raises(RuntimeError, match="Failed to register"):
                setup._chaoscenter_register_infra(
                    "http://localhost:9002/query", "pid", "env1", "ns", "tok",
                )


# ---------------------------------------------------------------------------
# ensure_chaoscenter_configured
# ---------------------------------------------------------------------------


class TestEnsureChaoscenterConfigured:
    def test_creates_env_and_infra_when_missing(self):
        setup = _make_setup()
        pod = _mock_pod("subscriber-abc")
        pod_list = MagicMock()
        pod_list.items = [pod]
        setup.core_api.list_namespaced_pod.return_value = pod_list

        with patch.object(
            setup,
            "_chaoscenter_login",
            return_value=("tok", "pid"),
        ), patch.object(
            setup,
            "_chaoscenter_list_environments",
            return_value=[],
        ), patch.object(
            setup,
            "_chaoscenter_create_environment",
            return_value="chaosprobe-myns",
        ) as mock_create_env, patch.object(
            setup,
            "_chaoscenter_list_infras",
            return_value=[],
        ), patch.object(
            setup,
            "_chaoscenter_register_infra",
            return_value={"infraID": "i1", "manifest": "yaml"},
        ) as mock_reg, patch.object(
            setup,
            "_apply_manifest",
        ) as mock_apply:
            result = setup.ensure_chaoscenter_configured(
                namespace="myns", base_host="http://localhost",
            )

        assert result["infra_id"] == "i1"
        assert result["environment_id"] == "chaosprobe-myns"
        mock_create_env.assert_called_once()
        mock_reg.assert_called_once()
        mock_apply.assert_called_once_with("yaml", "myns")

    def test_skips_existing_active_infra(self):
        setup = _make_setup()
        with patch.object(
            setup,
            "_chaoscenter_login",
            return_value=("tok", "pid"),
        ), patch.object(
            setup,
            "_chaoscenter_list_environments",
            return_value=[{"environmentID": "chaosprobe-ns", "name": "chaosprobe-ns"}],
        ), patch.object(
            setup,
            "_chaoscenter_list_infras",
            return_value=[
                {
                    "infraID": "existing",
                    "infraNamespace": "ns",
                    "environmentID": "chaosprobe-ns",
                    "isActive": True,
                }
            ],
        ), patch.object(
            setup,
            "_chaoscenter_register_infra",
        ) as mock_reg:
            result = setup.ensure_chaoscenter_configured(
                namespace="ns", base_host="http://localhost",
            )

        assert result["infra_id"] == "existing"
        mock_reg.assert_not_called()

    def test_does_not_reregister_inactive_infra(self):
        """Inactive infra (subscriber pending) should NOT create a duplicate."""
        setup = _make_setup()
        # Subscriber deployment exists
        setup.apps_api.read_namespaced_deployment.return_value = MagicMock()
        # Subscriber pod exists but not ready yet
        pod = _mock_pod("subscriber-abc", ready=False)
        pod_list_not_ready = MagicMock()
        pod_list_not_ready.items = [pod]
        # After a cycle, pod becomes ready
        pod_ready = _mock_pod("subscriber-abc", ready=True)
        pod_list_ready = MagicMock()
        pod_list_ready.items = [pod_ready]
        setup.core_api.list_namespaced_pod.side_effect = [
            pod_list_not_ready, pod_list_ready,
        ]

        with patch.object(
            setup,
            "_chaoscenter_login",
            return_value=("tok", "pid"),
        ), patch.object(
            setup,
            "_chaoscenter_list_environments",
            return_value=[{"environmentID": "chaosprobe-ns", "name": "chaosprobe-ns"}],
        ), patch.object(
            setup,
            "_chaoscenter_list_infras",
            return_value=[
                {
                    "infraID": "existing-inactive",
                    "infraNamespace": "ns",
                    "environmentID": "chaosprobe-ns",
                    "isActive": False,
                    "isInfraConfirmed": False,
                }
            ],
        ), patch.object(
            setup,
            "_chaoscenter_register_infra",
        ) as mock_reg, patch("time.sleep"):
            result = setup.ensure_chaoscenter_configured(
                namespace="ns", base_host="http://localhost",
            )

        assert result["infra_id"] == "existing-inactive"
        mock_reg.assert_not_called()  # Must NOT re-register

    def test_raises_on_missing_project_id(self):
        setup = _make_setup()
        with patch.object(
            setup,
            "_chaoscenter_login",
            return_value=("tok", ""),
        ):
            with pytest.raises(RuntimeError, match="projectID"):
                setup.ensure_chaoscenter_configured(
                    namespace="ns", base_host="http://localhost",
                )


# ---------------------------------------------------------------------------
# URL helper methods
# ---------------------------------------------------------------------------


class TestChaoscenterUrlHelpers:
    def test_gql_url(self):
        setup = _make_setup()
        assert setup._chaoscenter_gql_url("http://localhost") == \
            f"http://localhost:{LitmusSetup.CHAOSCENTER_SERVER_PORT}/query"

    def test_auth_url(self):
        setup = _make_setup()
        assert setup._chaoscenter_auth_url("http://localhost") == \
            f"http://localhost:{LitmusSetup.CHAOSCENTER_AUTH_PORT}"


# ---------------------------------------------------------------------------
# ChaosCenter experiment registration (save + run)
# ---------------------------------------------------------------------------


class TestChaoscenterSaveExperiment:
    @patch.object(LitmusSetup, "_chaoscenter_api_request")
    def test_save_experiment_calls_graphql(self, mock_req):
        mock_req.return_value = {"data": {"saveChaosExperiment": "exp-123"}}
        setup = _make_setup()
        result = setup.chaoscenter_save_experiment(
            gql_url="http://localhost:9002/query",
            project_id="proj-1",
            token="tok",
            infra_id="infra-1",
            experiment_id="exp-123",
            name="pod-delete-test",
            manifest="apiVersion: argoproj.io/v1alpha1\nkind: Workflow",
        )
        assert result == "exp-123"
        call_args = mock_req.call_args
        variables = call_args[1]["data"]["variables"] if "data" in call_args[1] else call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("data", {}).get("variables", {})
        # Just verify the request was made with correct URL and token
        mock_req.assert_called_once()

    @patch.object(LitmusSetup, "_chaoscenter_api_request")
    def test_run_experiment_returns_notify_id(self, mock_req):
        mock_req.return_value = {
            "data": {"runChaosExperiment": {"notifyID": "notify-abc"}}
        }
        setup = _make_setup()
        result = setup.chaoscenter_run_experiment(
            gql_url="http://localhost:9002/query",
            project_id="proj-1",
            token="tok",
            experiment_id="exp-123",
        )
        assert result == "notify-abc"

    @patch.object(LitmusSetup, "_chaoscenter_api_request")
    def test_get_experiment_run_returns_phase(self, mock_req):
        mock_req.return_value = {
            "data": {
                "getExperimentRun": {
                    "experimentRunID": "run-1",
                    "phase": "Completed",
                    "resiliencyScore": 100.0,
                    "faultsPassed": 1,
                    "faultsFailed": 0,
                    "faultsAwaited": 0,
                    "faultsStopped": 0,
                    "totalFaults": 1,
                }
            }
        }
        setup = _make_setup()
        result = setup.chaoscenter_get_experiment_run(
            gql_url="http://localhost:9002/query",
            project_id="proj-1",
            token="tok",
            notify_id="notify-abc",
        )
        assert result["phase"] == "Completed"
        assert result["resiliencyScore"] == 100.0
        assert result["totalFaults"] == 1


# ---------------------------------------------------------------------------
# ChaosRunner -- GraphQL-only execution
# ---------------------------------------------------------------------------

_CC_CONFIG = {
    "token": "tok",
    "project_id": "pid",
    "infra_id": "iid",
    "gql_url": "http://localhost:9002/query",
}

_ENGINE_SPEC = {
    "apiVersion": "litmuschaos.io/v1alpha1",
    "kind": "ChaosEngine",
    "metadata": {"name": "test-engine", "namespace": "online-boutique"},
    "spec": {
        "chaosServiceAccount": "litmus-admin",
        "experiments": [{"name": "pod-delete"}],
    },
}


def _make_runner(cc=None):
    """Create a ChaosRunner with LitmusSetup mocked."""
    from chaosprobe.chaos.runner import ChaosRunner

    with patch("chaosprobe.chaos.runner.LitmusSetup"):
        runner = ChaosRunner("test-ns", chaoscenter=cc or _CC_CONFIG)
    return runner


class TestChaosRunnerInit:
    def test_accepts_valid_config(self):
        runner = _make_runner()
        assert runner._cc == _CC_CONFIG

    def test_raises_without_config(self):
        from chaosprobe.chaos.runner import ChaosRunner

        with pytest.raises(ValueError, match="ChaosCenter configuration is required"):
            ChaosRunner("test-ns")

    def test_raises_with_none_config(self):
        from chaosprobe.chaos.runner import ChaosRunner

        with pytest.raises(ValueError, match="ChaosCenter configuration is required"):
            ChaosRunner("test-ns", chaoscenter=None)

    def test_raises_with_missing_keys(self):
        from chaosprobe.chaos.runner import ChaosRunner

        with patch("chaosprobe.chaos.runner.LitmusSetup"):
            with pytest.raises(ValueError, match="missing keys"):
                ChaosRunner("test-ns", chaoscenter={"token": "tok"})


class TestChaosRunnerBuildManifest:
    def test_workflow_structure(self):
        import yaml as _yaml

        runner = _make_runner(cc={**_CC_CONFIG, "infra_id": "test-infra-id"})
        manifest = runner._build_workflow_manifest(_ENGINE_SPEC, "pod-delete-engine", "inst-123")
        parsed = _yaml.safe_load(manifest)

        assert parsed["apiVersion"] == "argoproj.io/v1alpha1"
        assert parsed["kind"] == "Workflow"
        assert parsed["metadata"]["namespace"] == "test-ns"
        assert parsed["metadata"]["labels"]["infra_id"] == "test-infra-id"
        assert parsed["spec"]["serviceAccountName"] == "litmus-admin"
        assert len(parsed["spec"]["templates"]) == 3


class TestChaosRunnerRunExperiments:
    def test_save_run_poll_cycle(self):
        runner = _make_runner()
        runner._setup.chaoscenter_save_experiment.return_value = "exp-id"
        runner._setup.chaoscenter_run_experiment.return_value = "notify-id"
        runner._setup.chaoscenter_get_experiment_run.return_value = {
            "phase": "Completed",
            "resiliencyScore": 100.0,
            "faultsPassed": 1,
            "faultsFailed": 0,
            "totalFaults": 1,
        }

        results = runner.run_experiments([{"file": "test.yaml", "spec": _ENGINE_SPEC}])

        assert len(results) == 1
        assert results[0]["status"] == "Completed"
        assert results[0]["resiliencyScore"] == 100.0
        runner._setup.chaoscenter_save_experiment.assert_called_once()
        runner._setup.chaoscenter_run_experiment.assert_called_once()
        runner._setup.chaoscenter_get_experiment_run.assert_called_once()

    def test_save_failure_records_error(self):
        runner = _make_runner()
        runner._setup.chaoscenter_save_experiment.side_effect = RuntimeError("API down")

        results = runner.run_experiments([{"file": "t.yaml", "spec": _ENGINE_SPEC}])

        assert len(results) == 1
        assert results[0]["status"] == "error"
        assert "API down" in results[0]["error"]
        runner._setup.chaoscenter_run_experiment.assert_not_called()

    def test_run_failure_records_error(self):
        runner = _make_runner()
        runner._setup.chaoscenter_save_experiment.return_value = "eid"
        runner._setup.chaoscenter_run_experiment.side_effect = RuntimeError("trigger fail")

        results = runner.run_experiments([{"file": "t.yaml", "spec": _ENGINE_SPEC}])

        assert len(results) == 1
        assert results[0]["status"] == "error"
        assert "trigger fail" in results[0]["error"]

    @patch("chaosprobe.chaos.runner.time")
    def test_poll_timeout(self, mock_time):
        """Runner should return timeout status when phase never becomes terminal."""
        # time.time() is called many times: start_time, while-condition,
        # elapsed, end_time, etc.  Supply enough values then jump past timeout.
        times = [0] * 4 + [400] * 10
        mock_time.time.side_effect = times
        mock_time.sleep = MagicMock()

        runner = _make_runner()
        runner._setup.chaoscenter_save_experiment.return_value = "eid"
        runner._setup.chaoscenter_run_experiment.return_value = "nid"
        runner._setup.chaoscenter_get_experiment_run.return_value = {
            "phase": "Running",
        }

        results = runner.run_experiments([{"file": "t.yaml", "spec": _ENGINE_SPEC}])

        assert len(results) == 1
        assert results[0]["status"] == "timeout"

    def test_poll_transient_error_retries(self):
        """Transient errors during polling should be retried."""
        runner = _make_runner()
        runner._setup.chaoscenter_save_experiment.return_value = "eid"
        runner._setup.chaoscenter_run_experiment.return_value = "nid"
        runner._setup.chaoscenter_get_experiment_run.side_effect = [
            RuntimeError("transient"),
            {"phase": "Completed", "resiliencyScore": 80.0,
             "faultsPassed": 1, "faultsFailed": 0, "totalFaults": 1},
        ]

        with patch("chaosprobe.chaos.runner.time") as mock_time:
            mock_time.time.side_effect = [
                0,                  # start_time
                0, 0,               # poll loop iter 1: while check, elapsed
                5, 5,               # poll loop iter 2: while check, elapsed
                10, 10, 10,         # final elapsed + endTime + extra
            ]
            mock_time.sleep = MagicMock()
            results = runner.run_experiments([{"file": "t.yaml", "spec": _ENGINE_SPEC}])

        assert results[0]["status"] == "Completed"
        assert runner._setup.chaoscenter_get_experiment_run.call_count == 2

    def test_get_executed_experiments(self):
        runner = _make_runner()
        assert runner.get_executed_experiments() == []

        runner._setup.chaoscenter_save_experiment.return_value = "eid"
        runner._setup.chaoscenter_run_experiment.return_value = "nid"
        runner._setup.chaoscenter_get_experiment_run.return_value = {
            "phase": "Completed", "resiliencyScore": 100.0,
            "faultsPassed": 1, "faultsFailed": 0, "totalFaults": 1,
        }
        runner.run_experiments([{"file": "t.yaml", "spec": _ENGINE_SPEC}])
        assert len(runner.get_executed_experiments()) == 1
