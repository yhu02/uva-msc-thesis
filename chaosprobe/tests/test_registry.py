"""Tests for in-cluster registry discovery / resolution helpers."""

from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException

from chaosprobe.provisioner.components import (
    _ComponentsMixin,
    get_registry_address,
    resolve_probe_registry,
)


def _svc(node_port):
    port = MagicMock()
    port.node_port = node_port
    svc = MagicMock()
    svc.spec.ports = [port]
    return svc


def _pod(phase, node_name):
    pod = MagicMock()
    pod.status.phase = phase
    pod.spec.node_name = node_name
    return pod


def _node(addresses):
    node = MagicMock()
    node.status.addresses = [MagicMock(type=t, address=a) for t, a in addresses]
    return node


def _core(svc=None, pods=None, node=None):
    core = MagicMock()
    core.read_namespaced_service.return_value = svc
    core.list_namespaced_pod.return_value = MagicMock(items=pods or [])
    core.read_node.return_value = node
    return core


class TestGetRegistryAddress:
    def test_happy_path(self):
        core = _core(
            svc=_svc(30500),
            pods=[_pod("Running", "cp1")],
            node=_node([("Hostname", "cp1"), ("InternalIP", "192.168.56.11")]),
        )
        assert get_registry_address(core) == "192.168.56.11:30500"

    def test_none_when_service_absent(self):
        core = MagicMock()
        core.read_namespaced_service.side_effect = ApiException(status=404)
        assert get_registry_address(core) is None

    def test_none_when_no_node_port(self):
        assert get_registry_address(_core(svc=_svc(None), pods=[_pod("Running", "cp1")])) is None

    def test_none_when_no_running_pod(self):
        core = _core(svc=_svc(30500), pods=[_pod("Pending", "cp1")])
        assert get_registry_address(core) is None

    def test_none_when_node_has_no_internal_ip(self):
        core = _core(
            svc=_svc(30500),
            pods=[_pod("Running", "cp1")],
            node=_node([("Hostname", "cp1")]),
        )
        assert get_registry_address(core) is None

    def test_none_when_pod_list_fails(self):
        core = _core(svc=_svc(30500))
        core.list_namespaced_pod.side_effect = ApiException(status=500)
        assert get_registry_address(core) is None

    def test_none_when_read_node_fails(self):
        core = _core(svc=_svc(30500), pods=[_pod("Running", "cp1")])
        core.read_node.side_effect = ApiException(status=500)
        assert get_registry_address(core) is None


class TestResolveProbeRegistry:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("CHAOSPROBE_REGISTRY", "myreg:5000")
        assert resolve_probe_registry(MagicMock()) == "myreg:5000"

    def test_uses_incluster_when_no_override(self, monkeypatch):
        monkeypatch.delenv("CHAOSPROBE_REGISTRY", raising=False)
        with patch(
            "chaosprobe.provisioner.components.get_registry_address",
            return_value="192.168.56.11:30500",
        ):
            assert resolve_probe_registry(MagicMock()) == "192.168.56.11:30500"

    def test_raises_when_no_registry_and_no_override(self, monkeypatch):
        # No env override and no in-cluster registry: must raise, never fall
        # back to GHCR. The error points the operator at `chaosprobe init`.
        monkeypatch.delenv("CHAOSPROBE_REGISTRY", raising=False)
        with patch(
            "chaosprobe.provisioner.components.get_registry_address",
            return_value=None,
        ):
            with pytest.raises(RuntimeError, match="chaosprobe init"):
                resolve_probe_registry(MagicMock())


class TestIsRegistryInstalled:
    def _mixin(self):
        mixin = _ComponentsMixin.__new__(_ComponentsMixin)
        mixin.apps_api = MagicMock()
        return mixin

    def test_true_when_deployment_exists(self):
        mixin = self._mixin()
        mixin.apps_api.read_namespaced_deployment.return_value = MagicMock()
        assert mixin.is_registry_installed() is True

    def test_false_when_absent(self):
        mixin = self._mixin()
        mixin.apps_api.read_namespaced_deployment.side_effect = ApiException(status=404)
        assert mixin.is_registry_installed() is False
