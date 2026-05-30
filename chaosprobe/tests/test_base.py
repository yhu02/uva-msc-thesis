"""Tests for chaosprobe.metrics.base pod helpers."""

from unittest.mock import MagicMock, patch

from chaosprobe.metrics.base import (
    _pod_has_python3,
    exec_in_pod,
    find_all_probe_pods,
    find_all_probe_pods_with_node,
    find_probe_pod,
    find_probe_pods_per_node,
    find_ready_pod,
    pod_has_shell,
)


def _ready_pod(name):
    cond = MagicMock()
    cond.type = "Ready"
    cond.status = "True"
    pod = MagicMock()
    pod.status.conditions = [cond]
    pod.metadata.name = name
    return pod


class TestFindReadyPod:
    def test_returns_name_of_ready_pod(self):
        core_api = MagicMock()
        core_api.list_namespaced_pod.return_value = MagicMock(items=[_ready_pod("frontend-abc")])
        assert find_ready_pod(core_api, "default", "frontend") == "frontend-abc"

    def test_returns_none_when_no_ready_pod(self):
        cond = MagicMock()
        cond.type = "Ready"
        cond.status = "False"
        not_ready = MagicMock()
        not_ready.status.conditions = [cond]
        core_api = MagicMock()
        core_api.list_namespaced_pod.return_value = MagicMock(items=[not_ready])
        assert find_ready_pod(core_api, "default", "frontend") is None

    def test_returns_none_on_api_error(self):
        core_api = MagicMock()
        core_api.list_namespaced_pod.side_effect = RuntimeError("boom")
        assert find_ready_pod(core_api, "default", "frontend") is None


class TestExecInPod:
    def test_returns_stdout(self):
        core_api = MagicMock()
        with patch("kubernetes.stream.stream", return_value="hello\n"):
            out = exec_in_pod(core_api, "default", "pod-1", ["echo", "hello"])
        assert out == "hello\n"

    def test_wraps_exception(self):
        core_api = MagicMock()
        with patch("kubernetes.stream.stream", side_effect=RuntimeError("boom")):
            out = exec_in_pod(core_api, "default", "pod-1", ["false"])
        assert out.startswith("ERROR:")


class TestPodHelperApiErrors:
    """Pod-discovery and capability helpers degrade to a safe empty result
    (and log at debug) when the kubernetes API raises, rather than
    propagating the error or swallowing it silently."""

    def _raising_api(self):
        core_api = MagicMock()
        core_api.list_namespaced_pod.side_effect = RuntimeError("boom")
        return core_api

    def test_find_probe_pod_returns_none_on_api_error(self):
        assert find_probe_pod(self._raising_api(), "ns") is None

    def test_find_probe_pods_per_node_returns_empty_on_api_error(self):
        assert find_probe_pods_per_node(self._raising_api(), "ns") == []

    def test_find_all_probe_pods_with_node_returns_empty_on_api_error(self):
        assert find_all_probe_pods_with_node(self._raising_api(), "ns") == []

    def test_find_all_probe_pods_returns_empty_on_api_error(self):
        assert find_all_probe_pods(self._raising_api(), "ns") == []

    def test_pod_has_shell_returns_false_on_exec_error(self):
        with patch("kubernetes.stream.stream", side_effect=RuntimeError("boom")):
            assert pod_has_shell(MagicMock(), "ns", "pod-a") is False

    def test_pod_has_python3_returns_false_on_exec_error(self):
        with patch("kubernetes.stream.stream", side_effect=RuntimeError("boom")):
            assert _pod_has_python3(MagicMock(), "ns", "pod-a") is False
