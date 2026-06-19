"""Tests for ChaosCenter dashboard integration."""

import json
import os
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from chaosprobe.provisioner import chaoscenter_api
from chaosprobe.provisioner.chaoscenter import _scheme_and_host
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
        # Pre-seed the managed-password cache so the CHAOSCENTER_MANAGED_PASS
        # property resolves deterministically and never touches the real
        # ~/.chaosprobe during tests (resolution itself is tested separately).
        setup._managed_pass = "Test1managed!"  # policy-compliant deterministic seed
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

    def test_loadbalancer_address_pending_returns_none(self):
        # An ingress entry can exist before the LB address is assigned;
        # both ip and hostname are None during that transient window.
        setup = _make_setup()
        svc = _mock_service(
            LitmusSetup.CHAOSCENTER_FRONTEND_SVC,
            svc_type="LoadBalancer",
            port=9091,
        )
        ingress_entry = MagicMock()
        ingress_entry.ip = None
        ingress_entry.hostname = None
        svc.status.load_balancer = MagicMock()
        svc.status.load_balancer.ingress = [ingress_entry]
        setup.core_api.read_namespaced_service.return_value = svc

        assert setup.get_dashboard_url() is None

    def test_loadbalancer_no_ingress_returns_none(self):
        setup = _make_setup()
        svc = _mock_service(
            LitmusSetup.CHAOSCENTER_FRONTEND_SVC,
            svc_type="LoadBalancer",
            port=9091,
        )
        svc.status.load_balancer = MagicMock()
        svc.status.load_balancer.ingress = []
        setup.core_api.read_namespaced_service.return_value = svc

        assert setup.get_dashboard_url() is None

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
            LitmusSetup.CHAOSCENTER_FRONTEND_SVC,
            svc_type="NodePort",
            node_port=30091,
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
            c
            for c in mock_run.call_args_list
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
            url="http://localhost",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )
        setup = _make_setup()
        with pytest.raises(RuntimeError, match="ChaosCenter API error 401"):
            setup._chaoscenter_api_request("http://localhost:9002/api/query")

    @patch("urllib.request.urlopen")
    def test_raises_on_graphql_error(self, mock_urlopen):
        setup = _make_setup()
        response_data = {
            "data": None,
            "errors": [{"message": "failed to unmarshal workflow manifest1"}],
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with pytest.raises(RuntimeError, match="GraphQL error.*unmarshal"):
            setup._chaoscenter_api_request(
                "http://localhost:9002/api/query",
                data={"query": "mutation { fail }"},
            )

    @patch("urllib.request.urlopen")
    def test_non_json_response_raises_clear_error(self, mock_urlopen):
        # A 200 with a non-JSON body (e.g. a proxy/gateway HTML error page)
        # must surface as a clear RuntimeError, not a bare JSONDecodeError.
        setup = _make_setup()
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html><body>502 Bad Gateway</body></html>"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with pytest.raises(RuntimeError, match="non-JSON response.*502 Bad Gateway"):
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
                "http://localhost:9003",
                "admin",
                "litmus",
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
                "http://localhost:9003",
                "admin",
                "litmus",
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
                    "http://localhost:9003",
                    "admin",
                    "wrong",
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
        with (
            patch.object(setup, "_check_kubectl", return_value=True),
            patch.object(setup, "_check_helm", return_value=True),
            patch.object(setup, "_check_ansible", return_value=False),
            patch.object(setup, "_check_python_venv", return_value=True),
            patch.object(setup, "_check_git", return_value=True),
            patch.object(setup, "_check_ssh", return_value=True),
            patch.object(setup, "_check_vagrant", return_value=False),
            patch.object(setup, "_check_libvirt", return_value={"all_ready": False}),
            patch.object(setup, "_check_cluster_access", return_value=True),
            patch.object(setup, "is_litmus_installed", return_value=True),
            patch.object(setup, "is_litmus_ready", return_value=True),
            patch.object(setup, "is_chaoscenter_installed", return_value=False),
            patch.object(setup, "is_chaoscenter_ready", return_value=False),
        ):
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

        with (
            patch.object(LitmusSetup, "is_chaoscenter_installed", return_value=False),
            patch.object(
                LitmusSetup,
                "get_chaoscenter_status",
                return_value={
                    "installed": False,
                    "ready": False,
                    "pods": [],
                    "frontend_url": None,
                },
            ),
        ):
            # Patch _k8s_initialized and required K8s APIs
            with (
                patch.object(LitmusSetup, "_k8s_initialized", True, create=True),
                patch.object(LitmusSetup, "core_api", MagicMock(), create=True),
                patch.object(LitmusSetup, "apps_api", MagicMock(), create=True),
            ):
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

        with (
            patch.object(LitmusSetup, "is_chaoscenter_installed", return_value=False),
            patch.object(LitmusSetup, "_k8s_initialized", True, create=True),
            patch.object(LitmusSetup, "core_api", MagicMock(), create=True),
            patch.object(LitmusSetup, "apps_api", MagicMock(), create=True),
        ):
            result = self._runner().invoke(main, ["dashboard", "open"])
        assert result.exit_code != 0
        assert "not installed" in result.output.lower()

    @patch.object(LitmusSetup, "__init__", lambda self, **kw: None)
    def test_dashboard_open_with_url(self):
        from chaosprobe.cli import main

        with (
            patch.object(LitmusSetup, "is_chaoscenter_installed", return_value=True),
            patch.object(LitmusSetup, "get_dashboard_url", return_value="http://10.0.0.1:30091"),
            patch.object(LitmusSetup, "_k8s_initialized", True, create=True),
            patch.object(LitmusSetup, "core_api", MagicMock(), create=True),
            patch.object(LitmusSetup, "apps_api", MagicMock(), create=True),
        ):
            result = self._runner().invoke(main, ["dashboard", "open"])
        assert result.exit_code == 0
        assert "http://10.0.0.1:30091" in result.output

    @patch.object(LitmusSetup, "__init__", lambda self, **kw: None)
    def test_dashboard_install_already_installed(self):
        from chaosprobe.cli import main

        with (
            patch.object(LitmusSetup, "is_chaoscenter_installed", return_value=True),
            patch.object(LitmusSetup, "get_dashboard_url", return_value="http://10.0.0.1:30091"),
            patch.object(LitmusSetup, "_k8s_initialized", True, create=True),
            patch.object(LitmusSetup, "core_api", MagicMock(), create=True),
            patch.object(LitmusSetup, "apps_api", MagicMock(), create=True),
        ):
            result = self._runner().invoke(main, ["dashboard", "install"])
        assert result.exit_code == 0
        assert "already installed" in result.output.lower()


# ---------------------------------------------------------------------------
# _chaoscenter_login — password cascade & auto-rotation
# ---------------------------------------------------------------------------


class TestExtractToken:
    """`_extract_token` reads the access token across litmus key spellings,
    coercing a missing token to ``""``."""

    @pytest.mark.parametrize(
        "resp,expected",
        [
            ({"accessToken": "a"}, "a"),
            ({"access_token": "b"}, "b"),
            ({"token": "c"}, "c"),
            ({}, ""),  # no token key at all
            ({"accessToken": "", "token": "t"}, "t"),  # falsy first key falls through
        ],
    )
    def test_extract_token(self, resp, expected):
        assert chaoscenter_api._extract_token(resp) == expected


class TestChaoscenterLogin:
    def test_login_with_provided_password(self):
        setup = _make_setup()
        with patch.object(
            setup,
            "_chaoscenter_authenticate",
            return_value={"accessToken": "tok", "projectID": "p1"},
        ):
            token, pid = setup._chaoscenter_login(
                "http://localhost:9003",
                password="custom",
            )
        assert token == "tok"
        assert pid == "p1"

    def test_login_falls_back_to_managed_password(self):
        setup = _make_setup()
        calls = []

        def fake_auth(url, user, pwd):
            calls.append(pwd)
            if pwd == setup.CHAOSCENTER_MANAGED_PASS:
                return {"accessToken": "tok2", "projectID": "p2"}
            raise RuntimeError("bad")

        with patch.object(setup, "_chaoscenter_authenticate", side_effect=fake_auth):
            token, pid = setup._chaoscenter_login("http://localhost:9003")
        assert token == "tok2"
        assert setup.CHAOSCENTER_MANAGED_PASS in calls

    def test_login_auto_rotates_default_password(self, tmp_path, monkeypatch):
        # Isolate the password file: rotation now persists the managed password
        # only after change_password succeeds, so point it at a temp file (never
        # the real ~/.chaosprobe).
        pwfile = tmp_path / "pw"
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pwfile)
        setup = _make_setup()
        auth_calls = []

        def fake_auth(url, user, pwd):
            auth_calls.append(pwd)
            # Both managed and default work, but managed is tried first
            if pwd == setup.CHAOSCENTER_MANAGED_PASS:
                raise RuntimeError("bad")
            return {"accessToken": "tok", "projectID": "p1"}

        with patch.object(setup, "_chaoscenter_authenticate", side_effect=fake_auth):
            with patch.object(setup, "_chaoscenter_change_password") as mock_change:
                token, pid = setup._chaoscenter_login("http://localhost:9003")
        # Default password succeeded — should rotate to (compliant) managed and
        # persist it only after the rotation call succeeded.
        mock_change.assert_called_once()
        assert mock_change.call_args.args[3] == "Test1managed!"  # the rotation target
        assert pwfile.read_text() == "Test1managed!"  # persisted after success

    def test_login_upgrades_noncompliant_managed_target_on_rotation(self, tmp_path, monkeypatch):
        # If the managed target is non-compliant, rotating TO it would be
        # rejected by litmus; since the default worked (instance not on the old
        # value), generate a fresh compliant target, rotate to it, and persist
        # that — never the non-compliant value.
        pwfile = tmp_path / "pw"
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pwfile)
        setup = _make_setup()
        setup._managed_pass = (
            "legacy24charNonCompliantTok"  # non-compliant: >16 chars and no special char
        )

        def fake_auth(url, user, pwd):
            if pwd == setup.CHAOSCENTER_DEFAULT_PASS:
                return {"accessToken": "tok", "projectID": "p1"}
            raise RuntimeError("bad")

        with patch.object(setup, "_chaoscenter_authenticate", side_effect=fake_auth):
            with patch.object(setup, "_chaoscenter_change_password") as mock_change:
                setup._chaoscenter_login("http://localhost:9003")
        target = mock_change.call_args.args[3]
        assert target != "legacy24charNonCompliantTok"
        assert chaoscenter_api._is_policy_compliant(target)
        assert pwfile.read_text() == target  # the compliant target was persisted
        # The in-memory cache was upgraded too, so a later login in the same
        # process serves the new compliant value (no re-rotation loop).
        assert setup.CHAOSCENTER_MANAGED_PASS == target

    def test_login_does_not_persist_when_rotation_fails(self, tmp_path, monkeypatch):
        # change_password failing (e.g. policy/transport error) must NOT persist
        # a password the live instance was never set to — otherwise the file
        # drifts from the instance (the orphaning bug).
        pwfile = tmp_path / "pw"
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pwfile)
        setup = _make_setup()

        def fake_auth(url, user, pwd):
            if pwd == setup.CHAOSCENTER_DEFAULT_PASS:
                return {"accessToken": "tok", "projectID": "p1"}
            raise RuntimeError("bad")

        with patch.object(setup, "_chaoscenter_authenticate", side_effect=fake_auth):
            with patch.object(
                setup, "_chaoscenter_change_password", side_effect=RuntimeError("policy")
            ):
                token, _pid = setup._chaoscenter_login("http://localhost:9003")
        assert token == "tok"  # falls back to the default-password token
        assert not pwfile.exists()  # nothing persisted

    def test_login_with_managed_does_not_rotate_or_persist(self, tmp_path, monkeypatch):
        # When the managed (persisted) password works, the live instance is
        # already on it — no rotation, no regeneration, no file write. This is
        # the steady state that must never re-mint/orphan the password.
        pwfile = tmp_path / "pw"
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pwfile)
        setup = _make_setup()

        def fake_auth(url, user, pwd):
            if pwd == setup.CHAOSCENTER_MANAGED_PASS:
                return {"accessToken": "tok", "projectID": "p1"}
            raise RuntimeError("bad")

        with patch.object(setup, "_chaoscenter_authenticate", side_effect=fake_auth):
            with patch.object(setup, "_chaoscenter_change_password") as mock_change:
                token, _pid = setup._chaoscenter_login("http://localhost:9003")
        assert token == "tok"
        mock_change.assert_not_called()
        assert not pwfile.exists()

    def test_login_rotation_consumes_relogin_response(self, tmp_path, monkeypatch):
        # Rotation happy-path: instance is on DEFAULT, so the managed login fails
        # first; default works and rotates; the re-login with the new password
        # succeeds and ITS token/projectID are returned (not the stale default
        # token). Guards the resp2 consumption the fix reworked.
        pwfile = tmp_path / "pw"
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pwfile)
        setup = _make_setup()  # managed = "Test1managed!" (compliant)
        managed_calls = {"n": 0}

        def fake_auth(url, user, pwd):
            if pwd == setup.CHAOSCENTER_MANAGED_PASS:
                managed_calls["n"] += 1
                if managed_calls["n"] == 1:
                    raise RuntimeError("instance still on default")  # pre-rotation
                return {"accessToken": "rotated-tok", "projectID": "p2"}  # post-rotation re-login
            if pwd == setup.CHAOSCENTER_DEFAULT_PASS:
                return {"accessToken": "default-tok", "projectID": "p0"}
            raise RuntimeError("bad")

        with patch.object(setup, "_chaoscenter_authenticate", side_effect=fake_auth):
            with patch.object(setup, "_chaoscenter_change_password"):
                token, pid = setup._chaoscenter_login("http://localhost:9003")
        assert token == "rotated-tok"  # from resp2, not the default-login token
        assert pid == "p2"
        assert pwfile.read_text() == "Test1managed!"  # persisted after the rotation

    def test_login_rejects_noncompliant_env_override(self, monkeypatch):
        # A non-compliant env override is a hard config error — fail clearly
        # rather than silently rotating to a generated value (which the env would
        # then permanently shadow, orphaning the instance).
        monkeypatch.setenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, "from-env")  # non-compliant
        setup = _make_setup()
        with pytest.raises(RuntimeError, match="violates ChaosCenter's password policy"):
            setup._chaoscenter_login("http://localhost:9003")

    def test_noncompliant_env_fails_fast_even_with_explicit_password(self, monkeypatch):
        # Contract: a non-compliant env override is a hard config error that
        # fails fast even when an explicit (valid) password is supplied — the
        # env var has top precedence in resolution and can't be the rotation
        # target, so it must be fixed/unset rather than silently worked around.
        # Pins the up-front guard placement (ahead of the candidate loop).
        monkeypatch.setenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, "from-env")  # non-compliant
        setup = _make_setup()
        with pytest.raises(RuntimeError, match="violates ChaosCenter's password policy"):
            setup._chaoscenter_login("http://localhost:9003", password="Valid1pass!")

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
                "http://localhost:9002/query",
                "pid",
                "tok",
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
                "http://localhost:9002/query",
                "pid",
                "tok",
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
            return_value={"data": {"createEnvironment": {"environmentID": "my-env"}}},
        ):
            eid = setup._chaoscenter_create_environment(
                "http://localhost:9002/query",
                "pid",
                "my-env",
                "tok",
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
                "http://localhost:9002/query",
                "pid",
                "env1",
                "ns",
                "tok",
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
                    "http://localhost:9002/query",
                    "pid",
                    "env1",
                    "ns",
                    "tok",
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

        with (
            patch.object(
                setup,
                "_chaoscenter_login",
                return_value=("tok", "pid"),
            ),
            patch.object(
                setup,
                "_chaoscenter_list_environments",
                return_value=[],
            ),
            patch.object(
                setup,
                "_chaoscenter_create_environment",
                return_value="chaosprobe-myns",
            ) as mock_create_env,
            patch.object(
                setup,
                "_chaoscenter_list_infras",
                return_value=[],
            ),
            patch.object(
                setup,
                "_chaoscenter_register_infra",
                return_value={"infraID": "i1", "manifest": "yaml"},
            ) as mock_reg,
            patch.object(
                setup,
                "_apply_manifest",
            ) as mock_apply,
            patch.object(
                setup,
                "_wait_for_infra_active",
                return_value=True,
            ),
        ):
            result = setup.ensure_chaoscenter_configured(
                namespace="myns",
                base_host="http://localhost",
            )

        assert result["infra_id"] == "i1"
        assert result["environment_id"] == "chaosprobe-myns"
        mock_create_env.assert_called_once()
        mock_reg.assert_called_once()
        mock_apply.assert_called_once_with("yaml", "myns")

    def test_skips_existing_active_infra(self):
        setup = _make_setup()
        with (
            patch.object(
                setup,
                "_chaoscenter_login",
                return_value=("tok", "pid"),
            ),
            patch.object(
                setup,
                "_chaoscenter_list_environments",
                return_value=[{"environmentID": "chaosprobe-ns", "name": "chaosprobe-ns"}],
            ),
            patch.object(
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
            ),
            patch.object(
                setup,
                "_chaoscenter_register_infra",
            ) as mock_reg,
        ):
            result = setup.ensure_chaoscenter_configured(
                namespace="ns",
                base_host="http://localhost",
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
            pod_list_not_ready,
            pod_list_ready,
        ]

        with (
            patch.object(
                setup,
                "_chaoscenter_login",
                return_value=("tok", "pid"),
            ),
            patch.object(
                setup,
                "_chaoscenter_list_environments",
                return_value=[{"environmentID": "chaosprobe-ns", "name": "chaosprobe-ns"}],
            ),
            patch.object(
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
            ),
            patch.object(
                setup,
                "_chaoscenter_register_infra",
            ) as mock_reg,
            patch.object(
                setup,
                "_wait_for_infra_active",
                return_value=True,
            ),
            patch("time.sleep"),
        ):
            result = setup.ensure_chaoscenter_configured(
                namespace="ns",
                base_host="http://localhost",
            )

        assert result["infra_id"] == "existing-inactive"
        mock_reg.assert_not_called()  # Must NOT re-register

    def test_create_project_called_when_login_returns_no_project(self):
        # Fresh ChaosCenter: first login has no projectID → create_project is
        # invoked (mocked, no real network), then a re-login is attempted. Here
        # the re-login is still empty so it raises before the downstream bootstrap
        # — asserting the create+relogin contract. The happy continuation is
        # covered by the live smoke; failure/still-empty are tested below.
        setup = _make_setup()
        with (
            patch.object(setup, "_chaoscenter_login", return_value=("tok", "")) as mock_login,
            patch.object(setup, "_chaoscenter_create_project") as mock_create,
        ):
            with pytest.raises(RuntimeError, match="still returned no projectID"):
                setup.ensure_chaoscenter_configured(namespace="ns", base_host="http://localhost")
        mock_create.assert_called_once()
        assert mock_create.call_args.args[1] == "chaosprobe"  # (auth_url, project, token)
        assert mock_create.call_args.args[2] == "tok"
        assert mock_login.call_count == 2  # initial + re-login after create

    def test_raises_when_create_project_fails(self):
        setup = _make_setup()
        with (
            patch.object(setup, "_chaoscenter_login", return_value=("tok", "")),
            patch.object(setup, "_chaoscenter_create_project", side_effect=RuntimeError("api 401")),
        ):
            with pytest.raises(RuntimeError, match="project creation failed"):
                setup.ensure_chaoscenter_configured(namespace="ns", base_host="http://localhost")

    def test_create_project_endpoint_body_and_token(self):
        setup = _make_setup()
        with patch.object(setup, "_chaoscenter_api_request", return_value={}) as mock_req:
            setup._chaoscenter_create_project("http://localhost:9003", "chaosprobe", "tok")
        mock_req.assert_called_once()
        assert mock_req.call_args.args[0] == "http://localhost:9003/create_project"
        assert mock_req.call_args.kwargs["data"] == {"projectName": "chaosprobe"}
        assert mock_req.call_args.kwargs["token"] == "tok"


# ---------------------------------------------------------------------------
# URL helper methods
# ---------------------------------------------------------------------------


class TestChaoscenterUrlHelpers:
    def test_gql_url(self):
        setup = _make_setup()
        assert (
            setup._chaoscenter_gql_url("http://localhost")
            == f"http://localhost:{LitmusSetup.CHAOSCENTER_SERVER_PORT}/query"
        )

    def test_auth_url(self):
        setup = _make_setup()
        assert (
            setup._chaoscenter_auth_url("http://localhost")
            == f"http://localhost:{LitmusSetup.CHAOSCENTER_AUTH_PORT}"
        )


# ---------------------------------------------------------------------------
# ChaosCenter experiment registration (save + run)
# ---------------------------------------------------------------------------


class TestChaoscenterSaveExperiment:
    @patch.object(LitmusSetup, "_chaoscenter_api_request")
    def test_save_experiment_calls_graphql(self, mock_req):
        # First call: _chaoscenter_find_experiment_id (listExperiment)
        # Second call: saveChaosExperiment
        mock_req.side_effect = [
            {"data": {"listExperiment": {"totalNoOfExperiments": 0, "experiments": []}}},
            {"data": {"saveChaosExperiment": "exp-123"}},
        ]
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
        assert mock_req.call_count == 2

    @patch.object(LitmusSetup, "_chaoscenter_api_request")
    def test_run_experiment_returns_notify_id(self, mock_req):
        mock_req.return_value = {"data": {"runChaosExperiment": {"notifyID": "notify-abc"}}}
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
        import json as _json

        runner = _make_runner(cc={**_CC_CONFIG, "infra_id": "test-infra-id"})
        manifest, wf_name = runner._build_workflow_manifest(
            _ENGINE_SPEC, "pod-delete-engine", "inst-123"
        )
        # Must be valid JSON (not YAML) for ChaosCenter
        parsed = _json.loads(manifest)
        assert len(wf_name) <= 38

        assert parsed["apiVersion"] == "argoproj.io/v1alpha1"
        assert parsed["kind"] == "Workflow"
        assert parsed["metadata"]["namespace"] == "test-ns"
        assert parsed["metadata"]["labels"]["infra_id"] == "test-infra-id"
        assert parsed["spec"]["serviceAccountName"] == "litmus-admin"
        assert len(parsed["spec"]["templates"]) == 4
        # Verify install-chaos-faults template exists for ChaosCenter UI
        install_templates = [
            t for t in parsed["spec"]["templates"] if t["name"] == "install-chaos-faults"
        ]
        assert len(install_templates) == 1
        assert "artifacts" in install_templates[0]["inputs"]

    def test_engine_uses_generate_name(self):
        import json as _json

        import yaml as _yaml

        runner = _make_runner()
        manifest, _ = runner._build_workflow_manifest(_ENGINE_SPEC, "test", "inst-1")
        parsed = _json.loads(manifest)
        # Find the artifact template with the embedded ChaosEngine YAML
        run_template = [t for t in parsed["spec"]["templates"] if t["name"].startswith("run-")][0]
        engine_yaml = run_template["inputs"]["artifacts"][0]["raw"]["data"]
        engine = _yaml.safe_load(engine_yaml)
        assert "generateName" in engine["metadata"]
        assert "name" not in engine["metadata"]

    def test_engine_has_probe_ref_annotation(self):
        import json as _json

        import yaml as _yaml

        runner = _make_runner()
        manifest, _ = runner._build_workflow_manifest(_ENGINE_SPEC, "test", "inst-1")
        parsed = _json.loads(manifest)
        run_template = [t for t in parsed["spec"]["templates"] if t["name"].startswith("run-")][0]
        engine_yaml = run_template["inputs"]["artifacts"][0]["raw"]["data"]
        engine = _yaml.safe_load(engine_yaml)
        assert engine["metadata"]["annotations"]["probeRef"] == "[]"

    def test_fault_template_has_weight_label(self):
        import json as _json

        runner = _make_runner()
        manifest, _ = runner._build_workflow_manifest(_ENGINE_SPEC, "test", "inst-1")
        parsed = _json.loads(manifest)
        run_template = [t for t in parsed["spec"]["templates"] if t["name"].startswith("run-")][0]
        assert run_template["metadata"]["labels"]["weight"] == "10"


@patch("chaosprobe.orchestrator.portforward.check_port", return_value=True)
class TestChaosRunnerRunExperiments:
    def test_save_run_poll_cycle(self, _mock_port):
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

    def test_save_failure_raises_and_records_error(self, _mock_port):
        """Save failure raises so the iteration loop marks ERROR cleanly.

        Previously this returned silently with status='error' and let the
        iteration proceed to post-chaos sampling with no actual chaos run,
        producing a misleading 0-probe result that downstream analysis
        couldn't distinguish from a real catastrophic-resilience outcome.
        Now it raises; the error entry is still appended before raising
        so the iteration's executed_experiments list reflects what was
        attempted.
        """
        import pytest as _pytest

        runner = _make_runner()
        runner._setup.chaoscenter_save_experiment.side_effect = RuntimeError("API down")

        with _pytest.raises(RuntimeError, match="API down"):
            runner.run_experiments([{"file": "t.yaml", "spec": _ENGINE_SPEC}])

        # The error entry should still be recorded before raising.
        executed = runner.get_executed_experiments()
        assert len(executed) == 1
        assert executed[0]["status"] == "error"
        assert "API down" in executed[0]["error"]
        runner._setup.chaoscenter_run_experiment.assert_not_called()

    def test_run_failure_raises(self, _mock_port):
        """Trigger failure also raises (same reasoning as save)."""
        import pytest as _pytest

        runner = _make_runner()
        runner._setup.chaoscenter_save_experiment.return_value = "eid"
        runner._setup.chaoscenter_run_experiment.side_effect = RuntimeError("trigger fail")

        with _pytest.raises(RuntimeError, match="trigger fail"):
            runner.run_experiments([{"file": "t.yaml", "spec": _ENGINE_SPEC}])

    @patch("chaosprobe.chaos.runner.time")
    def test_poll_timeout(self, mock_time, _mock_port):
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

    def test_poll_transient_error_retries(self, _mock_port):
        """Transient errors during polling should be retried."""
        runner = _make_runner()
        runner._setup.chaoscenter_save_experiment.return_value = "eid"
        runner._setup.chaoscenter_run_experiment.return_value = "nid"
        runner._setup.chaoscenter_get_experiment_run.side_effect = [
            RuntimeError("transient"),
            {
                "phase": "Completed",
                "resiliencyScore": 80.0,
                "faultsPassed": 1,
                "faultsFailed": 0,
                "totalFaults": 1,
            },
        ]

        with patch("chaosprobe.chaos.runner.time") as mock_time:
            # _run_and_poll calls time.time() for startTime, then
            # _poll_experiment_run uses it for while check, elapsed,
            # heartbeat, etc.  Supply enough values.
            mock_time.time.side_effect = [
                0,  # _run_and_poll: start_time
                0,
                0,  # poll loop iter 1: while check, elapsed
                5,
                5,  # poll loop iter 2: while check, elapsed
                10,
                10,
                10,
                10,  # final elapsed + endTime + extra
            ]
            mock_time.sleep = MagicMock()
            results = runner.run_experiments([{"file": "t.yaml", "spec": _ENGINE_SPEC}])

        assert results[0]["status"] == "Completed"
        assert runner._setup.chaoscenter_get_experiment_run.call_count == 2

    def test_get_executed_experiments(self, _mock_port):
        runner = _make_runner()
        assert runner.get_executed_experiments() == []

        runner._setup.chaoscenter_save_experiment.return_value = "eid"
        runner._setup.chaoscenter_run_experiment.return_value = "nid"
        runner._setup.chaoscenter_get_experiment_run.return_value = {
            "phase": "Completed",
            "resiliencyScore": 100.0,
            "faultsPassed": 1,
            "faultsFailed": 0,
            "totalFaults": 1,
        }
        runner.run_experiments([{"file": "t.yaml", "spec": _ENGINE_SPEC}])
        assert len(runner.get_executed_experiments()) == 1


_ENGINE_SPEC_WITH_PROBES = {
    "apiVersion": "litmuschaos.io/v1alpha1",
    "kind": "ChaosEngine",
    "metadata": {"name": "test-engine-probes", "namespace": "online-boutique"},
    "spec": {
        "chaosServiceAccount": "litmus-admin",
        "experiments": [
            {
                "name": "pod-delete",
                "spec": {
                    "probe": [
                        {
                            "name": "http-probe-1",
                            "type": "httpProbe",
                            "mode": "Continuous",
                            "httpProbe/inputs": {
                                "url": "http://frontend:80",
                                "method": {"get": {"criteria": "==", "responseCode": "200"}},
                            },
                            "runProperties": {
                                "probeTimeout": "5s",
                                "interval": "2s",
                                "retry": 2,
                            },
                        },
                        {
                            "name": "http-probe-2",
                            "type": "httpProbe",
                            "mode": "Edge",
                            "httpProbe/inputs": {
                                "url": "http://frontend:80/health",
                                "method": {"get": {"criteria": "==", "responseCode": "200"}},
                            },
                            "runProperties": {
                                "probeTimeout": "10s",
                                "interval": "5s",
                                "retry": 3,
                            },
                        },
                    ],
                },
            },
        ],
    },
}


class TestChaosRunnerProbeRegistration:
    def test_register_probes_returns_probe_refs(self):
        """Registered probes should produce probeRef entries."""
        runner = _make_runner()
        runner._setup.chaoscenter_add_probe.return_value = {
            "name": "http-probe-1",
            "type": "httpProbe",
        }
        spec = deepcopy(_ENGINE_SPEC_WITH_PROBES)

        refs = runner._register_and_extract_probes(spec)

        assert len(refs) == 2
        assert refs[0] == {"probeID": "http-probe-1", "mode": "Continuous"}
        assert refs[1] == {"probeID": "http-probe-2", "mode": "Edge"}

    def test_inline_probes_kept_after_registration(self):
        """Inline probes must remain so the go-runner evaluates them."""
        runner = _make_runner()
        runner._setup.chaoscenter_add_probe.return_value = {"name": "x", "type": "httpProbe"}
        spec = deepcopy(_ENGINE_SPEC_WITH_PROBES)

        runner._register_and_extract_probes(spec)

        assert len(spec["spec"]["experiments"][0]["spec"]["probe"]) == 2

    def test_manifest_has_probe_ref_entries(self):
        """When probes are registered, the engine probeRef annotation should list them."""
        import json as _json

        import yaml as _yaml

        runner = _make_runner()
        runner._setup.chaoscenter_add_probe.return_value = {"name": "x", "type": "httpProbe"}
        spec = deepcopy(_ENGINE_SPEC_WITH_PROBES)
        probe_ref = runner._register_and_extract_probes(spec)

        manifest, _ = runner._build_workflow_manifest(
            spec, "test-probes", "inst-1", probe_ref=probe_ref
        )
        parsed = _json.loads(manifest)
        run_template = [t for t in parsed["spec"]["templates"] if t["name"].startswith("run-")][0]
        engine_yaml = run_template["inputs"]["artifacts"][0]["raw"]["data"]
        engine = _yaml.safe_load(engine_yaml)

        ref_list = _json.loads(engine["metadata"]["annotations"]["probeRef"])
        assert len(ref_list) == 2
        assert ref_list[0]["probeID"] == "http-probe-1"
        assert ref_list[1]["probeID"] == "http-probe-2"

    def test_no_probes_returns_empty_ref(self):
        """Engine spec without probes should return empty probeRef."""
        runner = _make_runner()
        refs = runner._register_and_extract_probes(deepcopy(_ENGINE_SPEC))
        assert refs == []


class TestExtractProbeVerdictsFromExecutionData:
    """Tests for _extract_probe_verdicts_from_execution_data."""

    def test_real_chaoscenter_format(self):
        """Parse verdicts from actual ChaosCenter executionData structure."""
        from chaosprobe.chaos.runner import _extract_probe_verdicts_from_execution_data

        execution_data = {
            "nodes": {
                "node-abc": {
                    "name": "run-pod-delete",
                    "phase": "Completed_With_Probe_Failure",
                    "type": "ChaosEngine",
                    "chaosData": {
                        "chaosResult": {
                            "status": {
                                "probeStatuses": [
                                    {
                                        "name": "probe-strict",
                                        "type": "httpProbe",
                                        "mode": "Continuous",
                                        "status": {
                                            "verdict": "Failed",
                                            "description": "500 != 200",
                                        },
                                    },
                                    {
                                        "name": "probe-loose",
                                        "type": "httpProbe",
                                        "mode": "Continuous",
                                        "status": {
                                            "verdict": "Passed",
                                            "description": "200 == 200",
                                        },
                                    },
                                ],
                            },
                        },
                    },
                },
                "node-def": {
                    "name": "steps",
                    "phase": "Succeeded",
                    "type": "Steps",
                },
            },
        }
        verdicts = _extract_probe_verdicts_from_execution_data(execution_data)
        assert verdicts == {"probe-strict": "Fail", "probe-loose": "Pass"}

    def test_json_string_input(self):
        """Accept JSON string as well as dict."""
        import json

        from chaosprobe.chaos.runner import _extract_probe_verdicts_from_execution_data

        data = {
            "nodes": {
                "n1": {
                    "chaosData": {
                        "chaosResult": {
                            "status": {
                                "probeStatuses": [
                                    {"name": "p1", "status": {"verdict": "Passed"}},
                                ],
                            },
                        },
                    },
                },
            },
        }
        verdicts = _extract_probe_verdicts_from_execution_data(json.dumps(data))
        assert verdicts == {"p1": "Pass"}

    def test_empty_input(self):
        from chaosprobe.chaos.runner import _extract_probe_verdicts_from_execution_data

        assert _extract_probe_verdicts_from_execution_data(None) == {}
        assert _extract_probe_verdicts_from_execution_data("") == {}
        assert _extract_probe_verdicts_from_execution_data({}) == {}

    def test_no_chaos_data_nodes(self):
        from chaosprobe.chaos.runner import _extract_probe_verdicts_from_execution_data

        data = {"nodes": {"n1": {"name": "steps", "type": "Steps"}}}
        assert _extract_probe_verdicts_from_execution_data(data) == {}


class TestSchemeAndHost:
    def test_strips_port(self):
        assert _scheme_and_host("http://localhost:9091") == "http://localhost"

    def test_no_port_kept_intact(self):
        # rsplit(":", 1) used to truncate this to just "http".
        assert _scheme_and_host("http://localhost") == "http://localhost"

    def test_strips_path_and_port(self):
        assert _scheme_and_host("http://node:9091/path") == "http://node"

    def test_https_with_ip(self):
        assert _scheme_and_host("https://10.0.0.5:9091") == "https://10.0.0.5"

    def test_ipv6_literal_rebracketed(self):
        assert _scheme_and_host("http://[::1]:9091") == "http://[::1]"

    def test_no_scheme_returns_bare_host(self):
        assert _scheme_and_host("") == ""


class TestResolveManagedPassword:
    """The managed ChaosCenter admin password is resolved at runtime — env var,
    then persisted file (verbatim), else None — never a source-committed default
    (REVIEW.md W2). Resolution is READ-ONLY: generation + persistence happen only
    after a successful rotation (see TestChaoscenterLogin), so the file never
    drifts ahead of the live instance."""

    def test_env_var_takes_precedence(self, tmp_path, monkeypatch):
        monkeypatch.setenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, "from-env")
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", tmp_path / "pw")
        assert chaoscenter_api._resolve_managed_password() == "from-env"
        # The env var wins without ever touching the file.
        assert not (tmp_path / "pw").exists()

    def test_reads_persisted_file(self, tmp_path, monkeypatch):
        # A persisted, policy-COMPLIANT password is reused verbatim.
        monkeypatch.delenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, raising=False)
        pw_file = tmp_path / "pw"
        pw_file.write_text("Persisted1!\n")
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pw_file)
        assert chaoscenter_api._resolve_managed_password() == "Persisted1!"

    def test_preserves_intentional_spaces_strips_only_newlines(self, tmp_path, monkeypatch):
        # Only line terminators are stripped — an intentional trailing space in
        # the stored password is preserved (strip() would have eaten it).
        monkeypatch.delenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, raising=False)
        pw_file = tmp_path / "pw"
        pw_file.write_text("Pass word1! \n")  # trailing space then newline
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pw_file)
        assert chaoscenter_api._resolve_managed_password() == "Pass word1! "

    def test_returns_none_when_absent(self, tmp_path, monkeypatch):
        # Read-only: with no env and no file, resolution returns None and does
        # NOT create the file — generation/persistence happens only after a
        # successful rotation, so the file never gets ahead of the instance.
        monkeypatch.delenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, raising=False)
        pw_file = tmp_path / "sub" / "pw"
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pw_file)
        assert chaoscenter_api._resolve_managed_password() is None
        assert not pw_file.exists()  # nothing written

    def test_undecodable_file_returns_none(self, tmp_path, monkeypatch):
        # A corrupt/non-UTF-8 file must be tolerated like any IO error (return
        # None) rather than raising UnicodeDecodeError and breaking auth.
        monkeypatch.delenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, raising=False)
        pw_file = tmp_path / "pw"
        pw_file.write_bytes(b"\xff\xfe\x00bad")  # invalid UTF-8
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pw_file)
        assert chaoscenter_api._resolve_managed_password() is None

    def test_persist_then_resolve_roundtrips_utf8(self, tmp_path, monkeypatch):
        # persist (UTF-8) and resolve (UTF-8) agree on encoding, so a non-ASCII
        # value round-trips intact.
        monkeypatch.delenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, raising=False)
        pw_file = tmp_path / "pw"
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pw_file)
        chaoscenter_api._persist_managed_password("Pä55wörd!")
        assert chaoscenter_api._resolve_managed_password() == "Pä55wörd!"

    def test_read_io_error_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, raising=False)
        a_dir = tmp_path / "iam_a_dir"
        a_dir.mkdir()
        # Pointing the password "file" at a directory makes the read (read_text)
        # raise OSError; resolution must tolerate it and return None, not crash.
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", a_dir)
        assert chaoscenter_api._resolve_managed_password() is None

    def test_persist_writes_0600(self, tmp_path, monkeypatch):
        pw_file = tmp_path / "sub" / "pw"
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pw_file)
        # Permissive umask (0): a create-then-chmod path would briefly produce a
        # 0666 file; the os.open(...,0o600) create must yield 0600 regardless.
        old_umask = os.umask(0)
        try:
            chaoscenter_api._persist_managed_password("Persisted9!")
        finally:
            os.umask(old_umask)
        assert pw_file.read_text() == "Persisted9!"
        assert oct(pw_file.stat().st_mode)[-3:] == "600"  # locked down

    def test_persist_rehardens_preexisting_loose_file(self, tmp_path, monkeypatch):
        # O_CREAT's mode only applies on creation, so a pre-existing world-readable
        # file must still be tightened to 0600 by the trailing chmod.
        pw_file = tmp_path / "pw"
        pw_file.write_text("old")
        pw_file.chmod(0o644)
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pw_file)
        chaoscenter_api._persist_managed_password("New1pass!")
        assert pw_file.read_text() == "New1pass!"
        assert oct(pw_file.stat().st_mode)[-3:] == "600"

    @pytest.mark.skipif(
        not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW unavailable on this platform"
    )
    def test_persist_does_not_follow_symlink(self, tmp_path, monkeypatch):
        # O_NOFOLLOW: a symlink planted at the password path must NOT be followed
        # — the open is refused (ELOOP -> OSError, tolerated) so the link's target
        # is never clobbered by the O_TRUNC write.
        victim = tmp_path / "victim"
        victim.write_text("important")
        link = tmp_path / "pw"
        link.symlink_to(victim)
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", link)
        chaoscenter_api._persist_managed_password("New1pass!")  # no raise
        assert victim.read_text() == "important"  # target untouched

    def test_persist_io_error_is_tolerated(self, tmp_path, monkeypatch):
        a_dir = tmp_path / "iam_a_dir"
        a_dir.mkdir()
        # Opening a directory for write raises OSError; persistence must swallow
        # it (logged at WARNING) rather than crash. Note: a failed persist is not
        # recoverable by re-derivation — the managed password is random — but
        # that is the caller's lockout risk, not this function's to raise on.
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", a_dir)
        chaoscenter_api._persist_managed_password("Persisted9!")  # no raise

    def test_no_committed_credential_in_source(self):
        from pathlib import Path as _Path

        src = _Path(chaoscenter_api.__file__).read_text()
        assert "ChaosProbe1!" not in src

    def test_property_resolves_once_and_caches(self, monkeypatch):
        calls = []

        def fake_resolve():
            calls.append(1)
            return "resolved-pw"

        monkeypatch.setattr(chaoscenter_api, "_resolve_managed_password", fake_resolve)
        setup = LitmusSetup.__new__(LitmusSetup)
        assert setup.CHAOSCENTER_MANAGED_PASS == "resolved-pw"
        assert setup.CHAOSCENTER_MANAGED_PASS == "resolved-pw"
        assert len(calls) == 1  # resolved once, then served from the instance cache

    def test_property_generates_compliant_when_unresolved(self, tmp_path, monkeypatch):
        # When nothing is resolved (no env, no file), the property falls back to
        # a freshly generated compliant secret WITHOUT persisting it — the file
        # is written only after a rotation sets it on ChaosCenter.
        monkeypatch.setattr(chaoscenter_api, "_resolve_managed_password", lambda: None)
        pwfile = tmp_path / "pw"
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pwfile)
        setup = LitmusSetup.__new__(LitmusSetup)
        pwd = setup.CHAOSCENTER_MANAGED_PASS
        assert chaoscenter_api._is_policy_compliant(pwd)
        assert not pwfile.exists()  # generated, not persisted here


# ---------------------------------------------------------------------------
# Password policy (litmus 3.x: 8–16 chars, 1 digit/lower/upper/special)
# ---------------------------------------------------------------------------


class TestPasswordPolicy:
    def test_generated_password_is_compliant(self):
        for _ in range(50):
            pwd = chaoscenter_api._generate_compliant_password()
            assert chaoscenter_api._is_policy_compliant(pwd), pwd

    @pytest.mark.parametrize(
        "pwd,ok",
        [
            ("Chaos1probe!", True),
            ("aB3$xyzq", True),  # exactly 8
            ("aB3$xyzqaB3$xyzq", True),  # exactly 16
            ("short1!A", True),
            ("nouppercase1!", False),  # no uppercase
            ("NOLOWERCASE1!", False),  # no lowercase
            ("NoDigitsHere!", False),  # no digit
            ("NoSpecial1Abc", False),  # no special
            ("Ab1!", False),  # too short (<8)
            ("Ab1!Ab1!Ab1!Ab1!x", False),  # 17 chars, too long
            ("VGhpc0lzMjRDaGFyc1Rva2Vu", False),  # token_urlsafe-style 24 chars
        ],
    )
    def test_is_policy_compliant(self, pwd, ok):
        assert chaoscenter_api._is_policy_compliant(pwd) is ok

    def test_resolve_reuses_noncompliant_persisted_password_verbatim(self, tmp_path, monkeypatch):
        # A persisted value is reused EXACTLY as stored, even if non-compliant —
        # it may be what a live ChaosCenter was set to (pre-policy), so silently
        # replacing it would orphan the instance. Upgrading a non-compliant value
        # happens only during a successful rotation (where the default proved the
        # instance is NOT on it), not here.
        pwfile = tmp_path / "chaoscenter-admin-password"
        pwfile.write_text("oldNonCompliantToken_urlsafe_24x")  # >16, non-compliant
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pwfile)
        monkeypatch.delenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, raising=False)
        assert chaoscenter_api._resolve_managed_password() == "oldNonCompliantToken_urlsafe_24x"
        assert pwfile.read_text().strip() == "oldNonCompliantToken_urlsafe_24x"  # NOT rewritten

    def test_resolve_rehardens_persisted_file_perms(self, tmp_path, monkeypatch):
        # A persisted compliant password whose file is world-readable must be
        # re-hardened to 0600 on reuse (it holds the admin password).

        pwfile = tmp_path / "chaoscenter-admin-password"
        pwfile.write_text("Chaos1probe!")
        pwfile.chmod(0o644)  # too permissive (e.g. created manually)
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pwfile)
        monkeypatch.delenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, raising=False)
        assert chaoscenter_api._resolve_managed_password() == "Chaos1probe!"
        assert (pwfile.stat().st_mode & 0o777) == 0o600  # re-hardened

    def test_resolve_keeps_compliant_persisted_password(self, tmp_path, monkeypatch):
        pwfile = tmp_path / "chaoscenter-admin-password"
        pwfile.write_text("Chaos1probe!")
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", pwfile)
        monkeypatch.delenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, raising=False)
        assert chaoscenter_api._resolve_managed_password() == "Chaos1probe!"

    def test_resolve_env_var_wins(self, tmp_path, monkeypatch):
        monkeypatch.setattr(chaoscenter_api, "CHAOSCENTER_PASSWORD_FILE", tmp_path / "nope")
        monkeypatch.setenv(chaoscenter_api.CHAOSCENTER_PASSWORD_ENV, "EnvP4ss!word")
        assert chaoscenter_api._resolve_managed_password() == "EnvP4ss!word"
